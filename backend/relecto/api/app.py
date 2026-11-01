"""Relecto web API + web application.

Run: ./relecto serve   (worker: ./relecto worker, DICOM receiver: ./relecto dicom)

Security model (edge node, inside the centre's network):
  - every API call except /api/health, /api/setup and /api/login needs a signed session cookie;
  - state-changing calls also need the X-Relecto header (blocks cross-site form posts);
  - roles: admin, radiologist (signs), secretary (uploads, indications, prints);
  - the audit log records who did what, never report text or images.

Report text tied to a study is the clinical record of the centre and is stored as written on
the centre's own server. The text-only checker (/api/check) keeps de-identifying everything.
"""

import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from .. import __version__
from ..auth import (COOKIE, ROLES, SESSION_SECONDS, CurrentUser, authenticate, check_password_policy,
                    create_user, current_user, hash_password, make_token, require, user_count)
from ..checker import Issue, Suggestion, apply_suggestion, run_check
from ..checker.content import get_content, reload_content
from ..db import (AuditEvent, CheckRun, Decision, Finding, Job, Report, Study, User, audit, data_dir,
                  session_scope)
from ..deid import deidentify

STATIC = Path(__file__).resolve().parent / "static"
OPEN_PATHS = {"/api/health", "/api/setup", "/api/login"}
ANY = require(*ROLES)
CAN_EDIT = require("admin", "radiologist")
CAN_SIGN = require("radiologist")
ADMIN = require("admin")

app = FastAPI(title="Relecto", version=__version__, docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=2048)


@app.middleware("http")
async def security(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") \
            and request.headers.get("x-relecto") != "1":
        return JSONResponse({"detail": "Missing X-Relecto header"}, status_code=403)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; "
                                               "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def _utc(d):
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(d):
    """ISO 8601 with timezone. SQLite returns naive datetimes: they are stored in UTC."""
    if not d:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat()


# ---------------------------------------------------------------- pages, health, settings

@app.get("/")
def index():
    return FileResponse(STATIC / "app.html")


@app.get("/checker")
def checker_page():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    c = get_content()
    from ..imaging import ai
    return {"status": "ok", "engine": __version__, "lexicon": c.lexicon.version,
            "rule_packs": {k: p.version for k, p in c.packs.items()},
            "ai_models": ai.available()}


def _settings_path():
    return data_dir() / "settings.json"


def load_settings() -> dict:
    p = _settings_path()
    base = {"centre_name": "Centre d'imagerie", "centre_address": "", "centre_phone": "", "report_footer": ""}
    if p.exists():
        base.update(json.loads(p.read_text()))
    return base


class SettingsIn(BaseModel):
    centre_name: str
    centre_address: str = ""
    centre_phone: str = ""
    report_footer: str = ""


@app.get("/api/settings")
def get_settings(user: CurrentUser = Depends(ANY)):
    return load_settings()


@app.put("/api/settings")
def put_settings(body: SettingsIn, user: CurrentUser = Depends(ADMIN)):
    _settings_path().write_text(json.dumps(body.model_dump(), ensure_ascii=False, indent=2))
    with session_scope() as s:
        audit(s, "settings.updated", user_id=user.id)
    return load_settings()


# ---------------------------------------------------------------- auth & users

class SetupIn(BaseModel):
    email: str
    name: str
    password: str
    title: Optional[str] = "Dr"
    centre_name: Optional[str] = None


class LoginIn(BaseModel):
    email: str
    password: str


def _set_session(resp: Response, request: Request, user_id: str):
    resp.set_cookie(COOKIE, make_token(user_id), max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                    secure=request.url.scheme == "https", path="/")


@app.get("/api/setup")
def setup_status():
    return {"needs_setup": user_count() == 0}


@app.post("/api/setup")
def setup(body: SetupIn, request: Request, response: Response):
    """Create the first administrator. Only possible while no account exists."""
    if user_count() > 0:
        raise HTTPException(409, "Déjà configuré")
    uid = create_user(body.email, body.name, "admin", body.password, body.title)
    if body.centre_name:
        st = load_settings()
        st["centre_name"] = body.centre_name
        _settings_path().write_text(json.dumps(st, ensure_ascii=False, indent=2))
    _set_session(response, request, uid)
    return {"ok": True}


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response):
    u = authenticate(body.email, body.password, request.client.host if request.client else "?")
    _set_session(response, request, u.id)
    return {"id": u.id, "name": u.name, "role": u.role}


@app.post("/api/logout")
def logout(response: Response, user: CurrentUser = Depends(current_user)):
    response.delete_cookie(COOKIE, path="/")
    with session_scope() as s:
        audit(s, "auth.logout", "user", user.id, user_id=user.id)
    return {"ok": True}


@app.get("/api/me")
def me(user: CurrentUser = Depends(current_user)):
    return user.to_dict()


class UserIn(BaseModel):
    email: str
    name: str
    role: Literal["admin", "radiologist", "secretary"]
    password: str
    title: Optional[str] = None


class UserPatch(BaseModel):
    name: Optional[str] = None
    role: Optional[Literal["admin", "radiologist", "secretary"]] = None
    active: Optional[bool] = None
    password: Optional[str] = None
    title: Optional[str] = None


class PasswordIn(BaseModel):
    current: str
    new: str


@app.get("/api/users")
def list_users(user: CurrentUser = Depends(ADMIN)):
    with session_scope() as s:
        return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role, "title": u.title,
                 "active": u.active, "last_login_at": _iso(u.last_login_at)}
                for u in s.query(User).order_by(User.name)]


@app.get("/api/radiologists")
def radiologists(user: CurrentUser = Depends(ANY)):
    with session_scope() as s:
        return [{"id": u.id, "name": u.name, "title": u.title}
                for u in s.query(User).filter(User.role == "radiologist", User.active.is_(True)).order_by(User.name)]


@app.post("/api/users")
def add_user(body: UserIn, user: CurrentUser = Depends(ADMIN)):
    return {"id": create_user(body.email, body.name, body.role, body.password, body.title)}


@app.patch("/api/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, user: CurrentUser = Depends(ADMIN)):
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise HTTPException(404, "Utilisateur inconnu")
        if user_id == user.id and (body.active is False or (body.role and body.role != "admin")):
            raise HTTPException(400, "Vous ne pouvez pas retirer vos propres droits d'administration.")
        changed = []
        for k in ("name", "role", "active", "title"):
            v = getattr(body, k)
            if v is not None:
                setattr(u, k, v)
                changed.append(k)
        if body.password:
            check_password_policy(body.password)
            u.password_hash = hash_password(body.password)
            changed.append("password")
        audit(s, "user.updated", "user", u.id, user_id=user.id, fields=changed)
    return {"ok": True}


@app.post("/api/me/password")
def change_password(body: PasswordIn, request: Request, user: CurrentUser = Depends(current_user)):
    authenticate(user.email, body.current, request.client.host if request.client else "?")
    check_password_policy(body.new)
    with session_scope() as s:
        s.get(User, user.id).password_hash = hash_password(body.new)
        audit(s, "user.password_changed", "user", user.id, user_id=user.id)
    return {"ok": True}


# ---------------------------------------------------------------- studies / worklist

def _study_row(st: Study, report: Optional[Report], names: dict) -> dict:
    job = st.jobs[-1] if st.jobs else None
    return {
        "id": st.id, "patient_name": st.patient_name, "patient_id": st.patient_id, "accession": st.accession,
        "modality": st.modality, "description": st.description, "study_date": st.study_date,
        "exam": st.exam, "status": st.status, "priority": st.priority, "n_images": st.n_images,
        "assigned_to": st.assigned_to, "assigned_name": names.get(st.assigned_to),
        "n_findings": len([f for f in st.findings if f.status != "rejected"]),
        "report_status": report.status if report else None,
        "signed_by": names.get(report.signed_by) if report else None,
        "job": {"status": job.status, "progress": job.progress} if job else None,
        "created_at": _iso(st.created_at), "source": st.source,
    }


@app.get("/api/studies")
def list_studies(status: Optional[str] = None, q: Optional[str] = None, mine: bool = False, limit: int = 200,
                 user: CurrentUser = Depends(ANY)):
    with session_scope() as s:
        query = select(Study).order_by(Study.priority.desc(), Study.created_at.desc()).limit(min(limit, 1000))
        if status == "todo":
            query = query.where(Study.status.in_(["received", "queued", "analyzing", "ready", "reported", "failed"]))
        elif status:
            query = query.where(Study.status == status)
        if q:
            like = f"%{q.strip()}%"
            query = query.where(or_(Study.patient_name.ilike(like), Study.patient_id.ilike(like),
                                    Study.accession.ilike(like), Study.description.ilike(like)))
        if mine:
            query = query.where(Study.assigned_to == user.id)
        studies = s.execute(query).scalars().all()
        reports = {r.study_id: r for r in s.execute(select(Report).where(Report.study_id.in_([x.id for x in studies]))).scalars()}
        names = {u.id: u.name for u in s.query(User)}
        rows = [_study_row(st, reports.get(st.id), names) for st in studies]
        counts = dict(s.execute(select(Study.status, func.count()).group_by(Study.status)).all())
    return {"studies": rows, "counts": counts}


@app.post("/api/studies/upload")
async def upload(file: UploadFile = File(...), user: CurrentUser = Depends(ANY)):
    from ..studies import ingest_zip
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Envoyez un fichier .zip contenant le dossier DICOM de l'examen.")
    import shutil
    import tempfile
    import zipfile
    # Spool to disk first: large studies never sit in memory, and zipfile gets a real file.
    with tempfile.NamedTemporaryFile(dir=data_dir(), suffix=".zip") as tmp:
        shutil.copyfileobj(file.file, tmp, length=1024 * 1024)
        tmp.flush()
        try:
            ids = ingest_zip(tmp.name, user_id=user.id)
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else "Archive .zip illisible.")
    return {"study_ids": ids}


def _get_study(s, study_id) -> Study:
    st = s.get(Study, study_id)
    if st is None:
        raise HTTPException(404, "Examen inconnu")
    return st


def _report_for(s, study_id) -> Optional[Report]:
    return s.execute(select(Report).where(Report.study_id == study_id)).scalar_one_or_none()


@app.get("/api/studies/{study_id}")
def get_study(study_id: str, user: CurrentUser = Depends(ANY)):
    with session_scope() as s:
        st = _get_study(s, study_id)
        rep = _report_for(s, study_id)
        names = {u.id: (f"{u.title} {u.name}".strip() if u.title else u.name) for u in s.query(User)}
        last = rep.checks[-1] if rep and rep.checks else None
        audit(s, "study.viewed", "study", st.id, user_id=user.id)
        return {
            **_study_row(st, rep, names),
            "patient_sex": st.patient_sex, "patient_birth_date": st.patient_birth_date,
            "indication": st.indication, "analysis": st.analysis,
            "findings": [{"id": f.id, "label": f.label, "kind": f.kind, "source": f.source, "status": f.status,
                          "decided_by": names.get(f.decided_by), **f.data} for f in st.findings],
            "jobs": [{"status": j.status, "progress": j.progress, "error": (j.error or "").split("\n")[0] or None,
                      "created_at": _iso(j.created_at), "finished_at": _iso(j.finished_at)} for j in st.jobs],
            "report": None if rep is None else {
                "id": rep.id, "status": rep.status, "text": rep.text_deid, "draft_text": rep.draft_text,
                "signed_at": _iso(rep.signed_at), "signed_by": names.get(rep.signed_by),
                "signed_versions": rep.signed_versions, "updated_at": _iso(rep.updated_at),
                "check": None if last is None else {"id": last.id, "issues": last.issues, "versions": last.versions},
            },
        }


class StudyPatch(BaseModel):
    indication: Optional[str] = None
    priority: Optional[Literal["routine", "urgent"]] = None
    assigned_to: Optional[str] = None
    exam: Optional[str] = None


@app.patch("/api/studies/{study_id}")
def patch_study(study_id: str, body: StudyPatch, user: CurrentUser = Depends(ANY)):
    with session_scope() as s:
        st = _get_study(s, study_id)
        fields = body.model_dump(exclude_unset=True)
        if "exam" in fields and fields["exam"] not in get_content().packs:
            raise HTTPException(400, "Examen inconnu")
        for k, v in fields.items():
            setattr(st, k, v or None if k in ("indication", "assigned_to") else v)
        audit(s, "study.updated", "study", st.id, user_id=user.id, fields=list(fields))
    return {"ok": True}


@app.post("/api/studies/{study_id}/analyze")
def reanalyze(study_id: str, user: CurrentUser = Depends(CAN_EDIT)):
    with session_scope() as s:
        st = _get_study(s, study_id)
        rep = _report_for(s, study_id)
        if rep and rep.status == "signed":
            raise HTTPException(409, "Compte rendu signé : l'examen ne peut plus être ré-analysé.")
        if any(j.status in ("queued", "running") for j in st.jobs):
            raise HTTPException(409, "Analyse déjà en cours")
        st.status = "queued"
        s.add(Job(study_id=st.id))
        audit(s, "analysis.requested", "study", st.id, user_id=user.id)
    return {"ok": True}


# ---------------------------------------------------------------- viewer data

@lru_cache(maxsize=8)
def _volume(path: str, mtime: float):
    return np.load(path, mmap_mode="r")


def _array(study_id: str, name: str):
    with session_scope() as s:
        st = _get_study(s, study_id)
        p = Path(st.storage_path).parent / "analysis" / f"{name}.npy"
    if not p.exists():
        raise HTTPException(404, "Données non disponibles (analyse non terminée)")
    return _volume(str(p), p.stat().st_mtime)


def _plane(vol, axis: str, idx: int, k_superior: bool = True):
    n = {"axial": vol.shape[0], "coronal": vol.shape[1], "sagittal": vol.shape[2]}[axis]
    if not 0 <= idx < n:
        raise HTTPException(404, "Coupe hors limites")
    if axis == "axial":
        return np.asarray(vol[idx])
    p = np.asarray(vol[:, idx, :]) if axis == "coronal" else np.asarray(vol[:, :, idx])
    return p[::-1] if k_superior else p      # superior at the top


@app.get("/api/studies/{study_id}/volume")
def volume_meta(study_id: str, user: CurrentUser = Depends(ANY)):
    vol = _array(study_id, "volume")
    with session_scope() as s:
        a = _get_study(s, study_id).analysis or {}
    p = Path(_get_path(study_id)) / "lobes.npy"
    return {"shape": list(vol.shape), "spacing_mm": a.get("spacing_mm"), "flip_lr": a.get("flip_lr", False),
            "k_superior": a.get("k_superior", True),
            "has_lobes": p.exists()}


def _get_path(study_id):
    with session_scope() as s:
        return str(Path(_get_study(s, study_id).storage_path).parent / "analysis")


@app.get("/api/studies/{study_id}/slice/{axis}/{idx}")
def get_slice(study_id: str, axis: Literal["axial", "coronal", "sagittal"], idx: int,
              layer: Literal["volume", "nodules", "lobes"] = "volume", user: CurrentUser = Depends(ANY)):
    """Raw pixels for the viewer: int16 HU (volume) or uint8 labels, little-endian, row-major.
    Windowing happens in the browser, so changing the window is instant."""
    with session_scope() as s:
        k_up = (_get_study(s, study_id).analysis or {}).get("k_superior", True)
    plane = _plane(_array(study_id, layer), axis, idx, k_up)
    dtype = "<i2" if layer == "volume" else "u1"
    data = np.ascontiguousarray(plane.astype(dtype)).tobytes()
    return Response(data, media_type="application/octet-stream",
                    headers={"X-Shape": f"{plane.shape[0]},{plane.shape[1]}", "Cache-Control": "private, max-age=3600"})


@app.get("/api/studies/{study_id}/key-image/{label}")
def key_image(study_id: str, label: str, user: CurrentUser = Depends(ANY)):
    p = Path(_get_path(study_id)) / f"{Path(label).name}.png"
    if not p.exists():
        raise HTTPException(404, "Image introuvable")
    return FileResponse(p, media_type="image/png")


# ---------------------------------------------------------------- findings

class FindingDecision(BaseModel):
    status: Literal["confirmed", "rejected", "proposed"]


@app.post("/api/findings/{finding_id}/decision")
def finding_decision(finding_id: str, body: FindingDecision, user: CurrentUser = Depends(CAN_EDIT)):
    with session_scope() as s:
        f = s.get(Finding, finding_id)
        if f is None:
            raise HTTPException(404, "Résultat inconnu")
        rep = _report_for(s, f.study_id)
        if rep and rep.status == "signed":
            raise HTTPException(409, "Compte rendu déjà signé")
        f.status, f.decided_by, f.decided_at = body.status, user.id, datetime.now(timezone.utc)
        audit(s, f"finding.{body.status}", "finding", f.id, user_id=user.id, study_id=f.study_id, label=f.label,
              source=f.source)
    return {"ok": True}


# ---------------------------------------------------------------- study report

class ReportIn(BaseModel):
    text: str


def _check_study_report(s, st: Study, text: str):
    from ..imaging.consistency import image_vs_report, reconcile_with_images
    from ..imaging.findings import NoduleFinding
    result = run_check(text, st.exam, max_issues=50)
    issues = list(result.issues)
    if st.exam == "ct_chest":
        nf = []
        for f in st.findings:
            if f.status == "rejected":
                continue
            d = {k: v for k, v in f.data.items() if k in NoduleFinding.__dataclass_fields__}
            nf.append(NoduleFinding(**d))
        img = image_vs_report(text, nf)
        issues = reconcile_with_images(issues, img, nf) + img
        issues.sort(key=lambda i: {"critical": 0, "warning": 1, "info": 2}.get(i.severity, 3))
    return result, [i.to_dict() for i in issues]


@app.put("/api/studies/{study_id}/report")
def save_report(study_id: str, body: ReportIn, user: CurrentUser = Depends(CAN_EDIT)):
    with session_scope() as s:
        st = _get_study(s, study_id)
        rep = _report_for(s, study_id)
        if rep is None:
            rep = Report(study_id=st.id, exam=st.exam, text_deid=body.text, text_hash=_hash(body.text), status="draft",
                         created_by=user.id)
            s.add(rep)
            s.flush()
        if rep.status == "signed":
            raise HTTPException(409, "Compte rendu déjà signé")
        result, issues = _check_study_report(s, st, body.text)
        rep.text_deid, rep.text_hash, rep.status = body.text, _hash(body.text), "checked"
        run = CheckRun(report_id=rep.id, text_hash=rep.text_hash, versions=result.versions, issues=issues,
                       duration_ms=result.ms)
        s.add(run)
        s.flush()
        if st.status in ("ready", "failed", "received"):
            st.status = "reported"
        audit(s, "report.saved", "report", rep.id, user_id=user.id, study_id=st.id, check_id=run.id,
              n_issues=len(issues), rules=[i["rule_id"] for i in issues])
        return {"report_id": rep.id, "check_id": run.id, "issues": issues, "versions": result.versions,
                "ms": result.ms}


class SignIn(BaseModel):
    text: str
    acknowledge_critical: bool = False


@app.post("/api/studies/{study_id}/report/sign")
def sign_study_report(study_id: str, body: SignIn, user: CurrentUser = Depends(CAN_SIGN)):
    with session_scope() as s:
        st = _get_study(s, study_id)
        rep = _report_for(s, study_id)
        if rep is None:
            raise HTTPException(404, "Aucun compte rendu")
        if rep.status == "signed":
            raise HTTPException(409, "Compte rendu déjà signé")
        if "[à compléter" in body.text:
            raise HTTPException(400, "Le compte rendu contient encore des champs « [à compléter …] ».")
        result, issues = _check_study_report(s, st, body.text)
        critical = [i for i in issues if i["severity"] == "critical"]
        if critical and not body.acknowledge_critical:
            raise HTTPException(409, {"message": "Points critiques non résolus", "issues": critical})
        now = datetime.now(timezone.utc)
        rep.text_deid, rep.text_hash = body.text, _hash(body.text)
        s.add(CheckRun(report_id=rep.id, text_hash=rep.text_hash, versions=result.versions, issues=issues,
                       duration_ms=result.ms))
        analysis = st.analysis or {}
        rep.status, rep.signed_at, rep.signed_by = "signed", now, user.id
        rep.signed_versions = {**result.versions, **{k: v for k, v in (analysis.get("versions") or {}).items()},
                               "findings_source": analysis.get("source")}
        st.status = "signed"
        changed = (rep.draft_text or "").strip() != body.text.strip()
        audit(s, "report.signed", "report", rep.id, user_id=user.id, study_id=st.id, text_hash=rep.text_hash,
              critical_acknowledged=len(critical), edited_from_draft=changed, versions=rep.signed_versions)
        return {"status": "signed", "signed_at": _iso(now), "versions": rep.signed_versions}


@app.get("/api/studies/{study_id}/report.pdf")
def report_pdf(study_id: str, user: CurrentUser = Depends(ANY)):
    from ..reporting import render_pdf
    with session_scope() as s:
        st = _get_study(s, study_id)
        rep = _report_for(s, study_id)
        if rep is None:
            raise HTTPException(404, "Aucun compte rendu")
        signer = s.get(User, rep.signed_by) if rep.signed_by else None
        pdf = render_pdf(load_settings(), st, rep, signer)
        audit(s, "report.printed", "report", rep.id, user_id=user.id, signed=rep.status == "signed")
    name = f"CR_{(st.accession or st.id)[:20]}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


# ---------------------------------------------------------------- text-only checker (kept)

class CheckIn(BaseModel):
    text: str
    exam: str = "ct_chest"
    use_llm: bool = False
    already_deidentified: bool = False
    report_id: Optional[str] = None


class SuggestionIn(BaseModel):
    start: Optional[int] = None
    end: Optional[int] = None
    original: Optional[str] = None
    replacement: str
    mode: Literal["replace", "append_conclusion"] = "replace"


class ApplyIn(BaseModel):
    text: str
    suggestion: SuggestionIn


class DecisionIn(BaseModel):
    report_id: str
    check_id: Optional[str] = None
    rule_id: str
    decision: Literal["accepted", "rejected"]


@app.get("/api/exams")
def exams(user: CurrentUser = Depends(ANY)):
    return [{"key": p.exam, "label": p.label, "version": p.version} for p in get_content().packs.values()]


@app.post("/api/check")
def check(body: CheckIn, user: CurrentUser = Depends(ANY)):
    if body.exam not in get_content().packs:
        raise HTTPException(400, f"Unknown exam {body.exam}")
    if body.already_deidentified:
        text, masked = body.text, {}
    else:
        d = deidentify(body.text)
        text, masked = d.text, d.counts()
    result = run_check(text, body.exam, use_llm=body.use_llm)
    issues = [i.to_dict() for i in result.issues]
    with session_scope() as s:
        report = s.get(Report, body.report_id) if body.report_id else None
        if body.report_id and report is None:
            raise HTTPException(404, "Unknown report")
        if report is not None and report.status == "signed":
            raise HTTPException(409, "Report already signed")
        if report is None:
            report = Report(exam=body.exam, text_deid=text, text_hash=_hash(text), created_by=user.id)
            s.add(report)
            s.flush()
            audit(s, "report.created", "report", report.id, user_id=user.id, exam=body.exam)
        else:
            report.text_deid, report.text_hash, report.exam = text, _hash(text), body.exam
        run = CheckRun(report_id=report.id, text_hash=_hash(text), versions=result.versions,
                       issues=issues, duration_ms=result.ms)
        s.add(run)
        s.flush()
        audit(s, "report.checked", "report", report.id, user_id=user.id, check_id=run.id, n_issues=len(issues),
              rules=[i["rule_id"] for i in issues], versions=result.versions)
        report_id, check_id = report.id, run.id
    return {"report_id": report_id, "check_id": check_id, "text": text, "masked": masked,
            "issues": issues, "ms": result.ms, "versions": result.versions}


@app.post("/api/apply")
def apply(body: ApplyIn, user: CurrentUser = Depends(ANY)):
    issue = Issue("UI", "info", "", "", "", suggestion=Suggestion(**body.suggestion.model_dump()))
    try:
        return {"text": apply_suggestion(body.text, issue)}
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/decision")
def decision(body: DecisionIn, user: CurrentUser = Depends(ANY)):
    with session_scope() as s:
        report = s.get(Report, body.report_id)
        if report is None:
            raise HTTPException(404, "Unknown report")
        run = s.get(CheckRun, body.check_id) if body.check_id else None
        s.add(Decision(report_id=report.id, check_id=body.check_id, rule_id=body.rule_id, user_id=user.id,
                       decision=body.decision, versions=run.versions if run else None))
        audit(s, f"suggestion.{body.decision}", "report", report.id, user_id=user.id, rule=body.rule_id,
              check_id=body.check_id)
    return {"ok": True}


@app.post("/api/reports/{report_id}/sign")
def sign(report_id: str, user: CurrentUser = Depends(CAN_SIGN)):
    with session_scope() as s:
        report = s.get(Report, report_id)
        if report is None:
            raise HTTPException(404, "Unknown report")
        last = report.checks[-1] if report.checks else None
        report.status, report.signed_at, report.signed_by = "signed", datetime.now(timezone.utc), user.id
        report.signed_versions = last.versions if last else None
        audit(s, "report.signed", "report", report.id, user_id=user.id, versions=report.signed_versions)
        return {"report_id": report.id, "status": report.status, "versions": report.signed_versions}


# ---------------------------------------------------------------- quality & audit

@app.get("/api/stats")
def stats(user: CurrentUser = Depends(ANY)):
    """Pilot metrics: suggestion acceptance per rule, AI finding confirmation, turnaround."""
    with session_scope() as s:
        rows = s.execute(select(Decision.rule_id, Decision.decision, func.count())
                         .group_by(Decision.rule_id, Decision.decision)).all()
        per = {}
        for rule, dec, n in rows:
            per.setdefault(rule, {"accepted": 0, "rejected": 0})[dec] = n
        for d in per.values():
            total = d["accepted"] + d["rejected"]
            d["acceptance"] = round(d["accepted"] / total, 2) if total else None
        fstat = dict(s.execute(select(Finding.status, func.count()).group_by(Finding.status)).all())
        signed = s.execute(select(Report).where(Report.study_id.is_not(None), Report.status == "signed")).scalars().all()
        studies = {st.id: st for st in s.execute(select(Study).where(Study.id.in_([r.study_id for r in signed]))).scalars()}
        tat = [(_utc(r.signed_at) - _utc(studies[r.study_id].created_at)).total_seconds() / 60
               for r in signed if r.signed_at and r.study_id in studies]
        edited = sum(1 for r in signed if (r.draft_text or "").strip() != (r.text_deid or "").strip())
    return {"rules": per, "findings": fstat, "signed_reports": len(signed),
            "median_turnaround_min": round(float(np.median(tat)), 1) if tat else None,
            "drafts_edited": edited}


@app.get("/api/audit")
def audit_log(limit: int = 200, user: CurrentUser = Depends(ADMIN)):
    with session_scope() as s:
        names = {u.id: u.name for u in s.query(User)}
        ev = s.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 2000)).all()
        return [{"ts": _iso(e.ts), "user": names.get(e.user_id), "action": e.action, "object_type": e.object_type,
                 "object_id": e.object_id, "details": e.details} for e in ev]


@app.post("/api/admin/reload-content")
def reload(user: CurrentUser = Depends(ADMIN)):
    c = reload_content()
    return {"lexicon": c.lexicon.version, "rule_packs": {k: p.version for k, p in c.packs.items()}}
