"""Relecto web API + local web app.

Run: ./relecto serve   (or: uvicorn relecto.api.app:app)

Every report is de-identified BEFORE it is checked or stored. The database
keeps only de-identified text; the audit log never contains report text.
Login and roles arrive in R1 part 2 (Keycloak/OIDC); until then the API is
meant for local use only (127.0.0.1).
"""

import hashlib
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import __version__
from ..checker import Issue, Suggestion, apply_suggestion, run_check
from ..checker.content import get_content, reload_content
from ..db import CheckRun, Decision, Report, audit, session_scope
from ..deid import deidentify

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Relecto", version=__version__)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    c = get_content()
    return {"status": "ok", "engine": __version__, "lexicon": c.lexicon.version,
            "rule_packs": {k: p.version for k, p in c.packs.items()}}


@app.get("/api/exams")
def exams():
    return [{"key": p.exam, "label": p.label, "version": p.version} for p in get_content().packs.values()]


@app.post("/api/check")
def check(body: CheckIn):
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
            report = Report(exam=body.exam, text_deid=text, text_hash=_hash(text))
            s.add(report)
            s.flush()
            audit(s, "report.created", "report", report.id, exam=body.exam)
        else:
            report.text_deid, report.text_hash, report.exam = text, _hash(text), body.exam
        run = CheckRun(report_id=report.id, text_hash=_hash(text), versions=result.versions,
                       issues=issues, duration_ms=result.ms)
        s.add(run)
        s.flush()
        audit(s, "report.checked", "report", report.id, check_id=run.id, n_issues=len(issues),
              rules=[i["rule_id"] for i in issues], versions=result.versions)
        report_id, check_id = report.id, run.id

    return {"report_id": report_id, "check_id": check_id, "text": text, "masked": masked,
            "issues": issues, "ms": result.ms, "versions": result.versions}


@app.post("/api/apply")
def apply(body: ApplyIn):
    issue = Issue("UI", "info", "", "", "", suggestion=Suggestion(**body.suggestion.model_dump()))
    try:
        return {"text": apply_suggestion(body.text, issue)}
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/decision")
def decision(body: DecisionIn):
    with session_scope() as s:
        report = s.get(Report, body.report_id)
        if report is None:
            raise HTTPException(404, "Unknown report")
        run = s.get(CheckRun, body.check_id) if body.check_id else None
        s.add(Decision(report_id=report.id, check_id=body.check_id, rule_id=body.rule_id,
                       decision=body.decision, versions=run.versions if run else None))
        audit(s, f"suggestion.{body.decision}", "report", report.id, rule=body.rule_id, check_id=body.check_id)
    return {"ok": True}


@app.post("/api/reports/{report_id}/sign")
def sign(report_id: str):
    from datetime import datetime, timezone
    with session_scope() as s:
        report = s.get(Report, report_id)
        if report is None:
            raise HTTPException(404, "Unknown report")
        last = report.checks[-1] if report.checks else None
        report.status, report.signed_at = "signed", datetime.now(timezone.utc)
        report.signed_versions = last.versions if last else None
        audit(s, "report.signed", "report", report.id, versions=report.signed_versions)
        return {"report_id": report.id, "status": report.status, "versions": report.signed_versions}


@app.get("/api/stats")
def stats():
    """Acceptance rate per rule: the pilot's key metric."""
    with session_scope() as s:
        rows = s.execute(select(Decision.rule_id, Decision.decision, func.count())
                         .group_by(Decision.rule_id, Decision.decision)).all()
    per = {}
    for rule, dec, n in rows:
        per.setdefault(rule, {"accepted": 0, "rejected": 0})[dec] = n
    for d in per.values():
        total = d["accepted"] + d["rejected"]
        d["acceptance"] = round(d["accepted"] / total, 2) if total else None
    return per


@app.post("/api/admin/reload-content")
def reload():
    c = reload_content()
    return {"lexicon": c.lexicon.version, "rule_packs": {k: p.version for k, p in c.packs.items()}}
