"""Platform end to end: accounts and roles, study intake, AI worker, viewer, report, signature, PDF."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import tests.conftest_env  # noqa: F401  (test database)
from relecto.db import AuditEvent, Finding, Job, Study, migrate, session_scope

migrate()
from fastapi.testclient import TestClient  # noqa: E402

from relecto.api.app import app  # noqa: E402
from relecto.auth import hash_password, make_token, read_token, verify_password  # noqa: E402
from relecto.imaging import ai  # noqa: E402
from relecto.worker import claim_job, run_job  # noqa: E402
from tests.auth_helpers import PASSWORD, client  # noqa: E402
from tests.synthetic_dicom import phantom, write_series, zip_folder  # noqa: E402


def fake_ai(nodule_mask):
    """Stand-in for the neural networks: returns known masks so the whole AI path is tested."""
    def lobes(ct, fast=None):
        out = np.zeros(ct.hu.shape, np.uint8)
        lung = ct.hu < -500
        x = np.arange(ct.hu.shape[2])[None, None, :]
        right = x < ct.hu.shape[2] // 2           # column index runs to patient left in the phantom
        out[lung & right] = 1
        out[lung & ~right] = 4
        out[nodule_mask & ~right] = 4
        return out
    return mock.patch.multiple(ai, available=lambda: True, segment_lobes=lobes,
                               detect_nodules=lambda ct, **kw: nodule_mask.copy(), versions=lambda: {"fake": "1"})


class TestAuth(unittest.TestCase):
    def test_password_hash(self):
        h = hash_password("Abcdefgh12")
        self.assertTrue(verify_password("Abcdefgh12", h))
        self.assertFalse(verify_password("abcdefgh12", h))
        self.assertNotIn("Abcdefgh12", h)

    def test_token_tamper(self):
        t = make_token("u1")
        self.assertEqual(read_token(t), "u1")
        self.assertIsNone(read_token(t.replace("u1.", "u2.")))
        self.assertIsNone(read_token(make_token("u1", now=0)))   # expired

    def test_weak_password_refused(self):
        admin = client("admin")
        r = admin.post("/api/users", json={"email": "x@t.tn", "name": "X", "role": "secretary", "password": "short"})
        self.assertEqual(r.status_code, 400)

    def test_login_throttled(self):
        c = TestClient(app)
        c.headers.update({"X-Relecto": "1"})
        client("secretary", name="Throttle")
        for _ in range(5):
            c.post("/api/login", json={"email": "nobody@t.tn", "password": "bad"})
        self.assertEqual(c.post("/api/login", json={"email": "nobody@t.tn", "password": "bad"}).status_code, 429)

    def test_roles(self):
        sec = client("secretary")
        self.assertEqual(sec.get("/api/users").status_code, 403)
        self.assertEqual(sec.get("/api/audit").status_code, 403)
        self.assertEqual(client("admin").get("/api/users").status_code, 200)


class TestStudyFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.hu, cls.nod = phantom(nodule_side="L", radius_mm=7.0)
        write_series(cls.tmp / "study", cls.hu, patient="BEN^SALAH")
        cls.sec, cls.rad, cls.admin = client("secretary"), client("radiologist", "Amira Test"), client("admin")

    def _upload_and_analyze(self, accession_zip):
        r = self.sec.post("/api/studies/upload", files={"file": ("exam.zip", io.BytesIO(accession_zip), "application/zip")})
        self.assertEqual(r.status_code, 200, r.text)
        sid = r.json()["study_ids"][0]
        with fake_ai(self.nod):
            claimed = claim_job()
            self.assertIsNotNone(claimed)
            self.assertTrue(run_job(*claimed))
        return sid

    def test_full_flow(self):
        folder = self.tmp / "flow"
        write_series(folder, self.hu, patient="BEN^SALAH", accession="FLOW1")
        sid = self._upload_and_analyze(zip_folder(folder))

        # worklist + study
        wl = self.rad.get("/api/studies?status=ready").json()["studies"]
        self.assertIn(sid, [s["id"] for s in wl])
        st = self.rad.get(f"/api/studies/{sid}").json()
        self.assertEqual(st["patient_name"], "BEN SALAH")
        self.assertEqual(st["status"], "ready")
        self.assertEqual(len(st["findings"]), 1)
        f = st["findings"][0]
        self.assertEqual((f["side"], f["lobe"]), ("L", "lobe supérieur gauche"))
        self.assertAlmostEqual(f["long_axis_mm"], 14, delta=3)
        self.assertIn("lobe supérieur gauche", st["report"]["text"])
        self.assertEqual(st["analysis"]["source"], "ai")

        # viewer data: raw HU slice and nodule labels
        r = self.rad.get(f"/api/studies/{sid}/slice/axial/{f['key_slice']}")
        self.assertEqual(r.headers["X-Shape"], "128,128")
        sl = np.frombuffer(r.content, "<i2").reshape(128, 128)
        self.assertEqual(int(sl.min()), -1000)
        lab = np.frombuffer(self.rad.get(f"/api/studies/{sid}/slice/axial/{f['key_slice']}?layer=nodules").content, "u1")
        self.assertGreater(int(lab.sum()), 0)
        self.assertEqual(self.rad.get(f"/api/studies/{sid}/slice/coronal/64").headers["X-Shape"], "60,128")
        self.assertEqual(self.rad.get(f"/api/studies/{sid}/slice/axial/999").status_code, 404)

        # secretary: indication yes, report edit / sign no
        self.assertEqual(self.sec.patch(f"/api/studies/{sid}", json={"indication": "Toux chronique"}).status_code, 200)
        self.assertEqual(self.sec.put(f"/api/studies/{sid}/report", json={"text": "x"}).status_code, 403)
        self.assertEqual(self.sec.post(f"/api/studies/{sid}/report/sign", json={"text": "x"}).status_code, 403)

        # radiologist deletes the nodule from the text -> IMG-001
        text = st["report"]["text"]
        removed = "\n".join(l for l in text.splitlines() if "mesurant" not in l)
        issues = self.rad.put(f"/api/studies/{sid}/report", json={"text": removed}).json()["issues"]
        self.assertIn("IMG-001", [i["rule_id"] for i in issues])
        # rejecting the AI finding removes the warning
        self.rad.post(f"/api/findings/{f['id']}/decision", json={"status": "rejected"})
        issues = self.rad.put(f"/api/studies/{sid}/report", json={"text": removed}).json()["issues"]
        self.assertNotIn("IMG-001", [i["rule_id"] for i in issues])
        self.rad.post(f"/api/findings/{f['id']}/decision", json={"status": "confirmed"})

        # placeholders block signing
        r = self.rad.post(f"/api/studies/{sid}/report/sign", json={"text": text})
        self.assertEqual(r.status_code, 400)
        final = text.replace("[à compléter par le radiologue]", "RAS").replace("INDICATION : RAS", "INDICATION : Toux chronique")
        r = self.rad.post(f"/api/studies/{sid}/report/sign", json={"text": final})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["versions"]["findings_source"], "ai")

        # locked after signature
        self.assertEqual(self.rad.put(f"/api/studies/{sid}/report", json={"text": "changed"}).status_code, 409)
        self.assertEqual(self.rad.post(f"/api/studies/{sid}/analyze").status_code, 409)

        # PDF
        pdf = self.sec.get(f"/api/studies/{sid}/report.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        # audit trail: who did what, never report text
        with session_scope() as s:
            ev = s.query(AuditEvent).filter(AuditEvent.object_id.in_([sid])).all()
            actions = {e.action for e in ev}
            self.assertTrue({"study.received", "analysis.done"} <= actions)
            all_details = str([e.details for e in s.query(AuditEvent).all()])
            self.assertNotIn("mesurant", all_details)
            self.assertNotIn(PASSWORD, all_details)

        stats = self.rad.get("/api/stats").json()
        self.assertGreaterEqual(stats["signed_reports"], 1)

    def test_reupload_same_study_is_not_duplicated(self):
        folder = self.tmp / "dup"
        uid = write_series(folder, self.hu, accession="DUP1")
        z = zip_folder(folder)
        r1 = self.sec.post("/api/studies/upload", files={"file": ("a.zip", io.BytesIO(z), "application/zip")}).json()
        r2 = self.sec.post("/api/studies/upload", files={"file": ("a.zip", io.BytesIO(z), "application/zip")}).json()
        self.assertEqual(r1["study_ids"], r2["study_ids"])
        with session_scope() as s:
            self.assertEqual(s.query(Study).filter(Study.study_uid == uid).count(), 1)
            self.assertEqual(s.query(Job).filter(Job.study_id == r1["study_ids"][0]).count(), 1)

    def test_bad_uploads(self):
        self.assertEqual(self.sec.post("/api/studies/upload", files={"file": ("a.txt", b"x", "text/plain")}).status_code, 400)
        buf = io.BytesIO()
        import zipfile
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../evil.txt", "not dicom")
        r = self.sec.post("/api/studies/upload", files={"file": ("a.zip", io.BytesIO(buf.getvalue()), "application/zip")})
        self.assertEqual(r.status_code, 400)

    def test_no_ai_no_reference_gives_honest_draft(self):
        folder = self.tmp / "noai"
        write_series(folder, self.hu, accession="NOAI")
        r = self.sec.post("/api/studies/upload", files={"file": ("a.zip", io.BytesIO(zip_folder(folder)), "application/zip")})
        sid = r.json()["study_ids"][0]
        with mock.patch.object(ai, "available", lambda: False):
            while True:
                c = claim_job()
                if not c:
                    break
                run_job(*c)
        st = self.rad.get(f"/api/studies/{sid}").json()
        self.assertEqual(st["analysis"]["source"], "none")
        self.assertEqual(st["findings"], [])
        self.assertIn("Parenchyme pulmonaire : [à compléter", st["report"]["text"])
        self.assertNotIn("Pas de nodule", st["report"]["text"])

    def test_admin_users_and_settings(self):
        r = self.admin.post("/api/users", json={"email": "sec2@t.tn", "name": "Sec Two", "role": "secretary",
                                                 "password": "Secretaire-2026"})
        self.assertEqual(r.status_code, 200)
        uid = r.json()["id"]
        self.assertEqual(self.admin.patch(f"/api/users/{uid}", json={"active": False}).status_code, 200)
        c = TestClient(app)
        c.headers.update({"X-Relecto": "1"})
        self.assertEqual(c.post("/api/login", json={"email": "sec2@t.tn", "password": "Secretaire-2026"}).status_code, 401)
        s = self.admin.put("/api/settings", json={"centre_name": "Centre Test", "centre_address": "Tunis"}).json()
        self.assertEqual(s["centre_name"], "Centre Test")
        me = self.admin.get("/api/me").json()
        self.assertEqual(self.admin.patch(f"/api/users/{me['id']}", json={"role": "secretary"}).status_code, 400)


class TestWorkerIsolation(unittest.TestCase):
    def test_job_runs_in_child_process(self):
        from relecto.studies import ingest_folder
        from relecto.worker import loop
        tmp = Path(tempfile.mkdtemp())
        hu, _ = phantom(n_slices=30, size=64)
        write_series(tmp, hu, accession="ISO1")
        sid = ingest_folder(tmp)[0]
        with mock.patch.dict("os.environ", {"RELECTO_DISABLE_AI": "1"}):
            loop(once=True, isolate=True)
        with session_scope() as s:
            st = s.get(Study, sid)
            self.assertEqual(st.status, "ready", [j.error for j in st.jobs])
            self.assertEqual(st.jobs[-1].status, "done")


class TestDicomIngest(unittest.TestCase):
    def test_non_ct_study_goes_to_manual_reporting(self):
        from relecto.studies import ingest_folder
        import pydicom
        tmp = Path(tempfile.mkdtemp())
        hu, _ = phantom(n_slices=1, size=64)
        write_series(tmp, hu, accession="XRAY1")
        p = next(tmp.glob("*.dcm"))
        ds = pydicom.dcmread(p)
        ds.Modality = "DX"
        ds.save_as(p)
        sid = ingest_folder(tmp)[0]
        with session_scope() as s:
            st = s.get(Study, sid)
            self.assertEqual(st.status, "ready")
            self.assertEqual(len(st.jobs), 0)

    def test_folder_import_groups_by_study(self):
        from relecto.studies import ingest_folder
        tmp = Path(tempfile.mkdtemp())
        hu, _ = phantom(n_slices=20, size=64)
        write_series(tmp / "a", hu, accession="IMPA")
        write_series(tmp / "b", hu, accession="IMPB")
        (tmp / "notes.txt").write_text("not dicom")
        ids = ingest_folder(tmp, queue=False)
        self.assertEqual(len(ids), 2)
        with session_scope() as s:
            for i in ids:
                self.assertEqual(s.get(Study, i).n_images, 20)


if __name__ == "__main__":
    unittest.main()
