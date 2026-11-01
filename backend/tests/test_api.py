import unittest

from tests.conftest_env import DEMO
from fastapi.testclient import TestClient

from relecto.api.app import app
from relecto.db import AuditEvent, Decision, Report, migrate, session_scope

migrate()
from tests.auth_helpers import client  # noqa: E402


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = client("radiologist")

    def test_health_reports_versions(self):
        r = self.c.get("/api/health").json()
        self.assertEqual(r["status"], "ok")
        self.assertIn("ct_chest", r["rule_packs"])

    def test_page_served(self):
        self.assertIn("Relecto", self.c.get("/").text)
        self.assertIn("Relecto", self.c.get("/checker").text)

    def test_login_required(self):
        anon = TestClient(app)
        anon.headers.update({"X-Relecto": "1"})
        self.assertEqual(anon.post("/api/check", json={"text": DEMO}).status_code, 401)
        self.assertEqual(anon.get("/api/studies").status_code, 401)

    def test_csrf_header_required(self):
        r = TestClient(app, cookies=self.c.cookies).post("/api/check", json={"text": DEMO})
        self.assertEqual(r.status_code, 403)

    def test_check_deidentifies_stores_and_audits(self):
        r = self.c.post("/api/check", json={"text": "Patient : BEN SALAH Mohamed\n" + DEMO, "exam": "ct_chest"}).json()
        self.assertNotIn("BEN SALAH", r["text"])
        self.assertIn("LAT-001", [i["rule_id"] for i in r["issues"]])
        with session_scope() as s:
            rep = s.get(Report, r["report_id"])
            self.assertNotIn("BEN SALAH", rep.text_deid)
            self.assertEqual(len(rep.checks), 1)
            self.assertEqual(rep.checks[0].versions, r["versions"])
            events = s.query(AuditEvent).filter_by(object_id=r["report_id"]).all()
            self.assertIn("report.checked", [e.action for e in events])
            self.assertNotIn("Nodule", str([e.details for e in events]))  # no report text in the audit log

    def test_apply_recheck_same_report(self):
        r = self.c.post("/api/check", json={"text": DEMO, "exam": "ct_chest"}).json()
        lat = next(i for i in r["issues"] if i["rule_id"] == "LAT-001")
        fixed = self.c.post("/api/apply", json={"text": r["text"], "suggestion": lat["suggestion"]}).json()["text"]
        r2 = self.c.post("/api/check", json={"text": fixed, "exam": "ct_chest", "already_deidentified": True,
                                             "report_id": r["report_id"]}).json()
        self.assertEqual(r2["report_id"], r["report_id"])
        self.assertNotIn("LAT-001", [i["rule_id"] for i in r2["issues"]])
        with session_scope() as s:
            self.assertEqual(len(s.get(Report, r["report_id"]).checks), 2)

    def test_decision_and_stats(self):
        r = self.c.post("/api/check", json={"text": DEMO, "exam": "ct_chest"}).json()
        self.c.post("/api/decision", json={"report_id": r["report_id"], "check_id": r["check_id"],
                                           "rule_id": "LAT-001", "decision": "accepted"})
        with session_scope() as s:
            d = s.query(Decision).filter_by(report_id=r["report_id"]).one()
            self.assertEqual(d.versions, r["versions"])
        self.assertGreaterEqual(self.c.get("/api/stats").json()["rules"]["LAT-001"]["accepted"], 1)

    def test_sign_locks_report(self):
        r = self.c.post("/api/check", json={"text": DEMO, "exam": "ct_chest"}).json()
        signed = self.c.post(f"/api/reports/{r['report_id']}/sign").json()
        self.assertEqual(signed["status"], "signed")
        self.assertEqual(signed["versions"], r["versions"])
        again = self.c.post("/api/check", json={"text": DEMO, "exam": "ct_chest", "report_id": r["report_id"]})
        self.assertEqual(again.status_code, 409)

    def test_unknown_exam_and_report(self):
        self.assertEqual(self.c.post("/api/check", json={"text": "x", "exam": "nope"}).status_code, 400)
        self.assertEqual(self.c.post("/api/decision", json={"report_id": "nope", "rule_id": "X",
                                                             "decision": "accepted"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
