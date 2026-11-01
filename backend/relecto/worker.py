"""AI worker: takes queued analysis jobs from the database and runs the imaging pipeline.

Run one per machine (CPU) or one per GPU:  python -m relecto worker
The database is the queue. On PostgreSQL several workers can run safely (SKIP LOCKED).
"""

import logging
import os
import socket
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .db import CheckRun, Finding, Job, Report, Study, audit, get_engine, session_scope

log = logging.getLogger("relecto.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _now():
    return datetime.now(timezone.utc)


def claim_job():
    with session_scope() as s:
        q = select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
        if get_engine().dialect.name == "postgresql":
            q = q.with_for_update(skip_locked=True)
        job = s.execute(q).scalar_one_or_none()
        if job is None:
            return None
        job.status, job.started_at, job.worker, job.progress = "running", _now(), WORKER_ID, "Chargement des images"
        st = s.get(Study, job.study_id)
        st.status = "analyzing"
        return job.id, st.id, st.storage_path, st.exam, st.indication


def set_progress(job_id, text):
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j:
            j.progress = text


def run_job(job_id, study_id, storage_path, exam, indication):
    from .imaging.pipeline import analyze_study

    out = Path(storage_path).parent / "analysis"
    try:
        a = analyze_study(storage_path, out, source=os.environ.get("RELECTO_FINDINGS_SOURCE", "auto"),
                          indication=indication, progress=lambda t: set_progress(job_id, t))
    except Exception as exc:
        log.error("Job %s failed: %s", job_id, exc)
        with session_scope() as s:
            j, st = s.get(Job, job_id), s.get(Study, study_id)
            j.status, j.finished_at, j.error = "failed", _now(), f"{exc}\n{traceback.format_exc()[-2000:]}"
            st.status = "failed"
            audit(s, "analysis.failed", "study", study_id, job_id=job_id, error=str(exc)[:300])
        return False

    with session_scope() as s:
        st, j = s.get(Study, study_id), s.get(Job, job_id)
        for f in list(st.findings):
            s.delete(f)
        s.flush()
        for f in a.findings:
            s.add(Finding(study_id=study_id, label=f.finding_id, kind=f.kind, source=f.source, data=f.to_dict()))
        st.analysis = a.summary()
        report = s.execute(select(Report).where(Report.study_id == study_id)).scalar_one_or_none()
        if report is None or report.status != "signed":
            if report is None:
                report = Report(study_id=study_id, exam=exam, text_deid=a.draft, text_hash="", status="draft")
                s.add(report)
            report.draft_text, report.text_deid, report.status = a.draft, a.draft, "draft"
            s.flush()
            checker_versions = {k[len("checker_"):]: v for k, v in a.versions.items() if k.startswith("checker_")}
            s.add(CheckRun(report_id=report.id, text_hash="", versions=checker_versions,
                           issues=[i.to_dict() for i in a.issues], duration_ms=a.seconds * 1000))
        st.status = "ready"
        j.status, j.finished_at, j.progress = "done", _now(), f"Terminé en {a.seconds} s"
        audit(s, "analysis.done", "study", study_id, job_id=job_id, findings=len(a.findings),
              source=a.source, seconds=a.seconds, versions=a.versions)
    return True


def loop(poll_seconds: float = 2.0, once: bool = False):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log.info("Worker %s started", WORKER_ID)
    with session_scope() as s:   # jobs left running by a crashed worker go back to the queue
        for j in s.execute(select(Job).where(Job.status == "running")).scalars():
            j.status, j.progress = "queued", "Relancé après redémarrage"
    while True:
        claimed = claim_job()
        if claimed:
            log.info("Analysing study %s", claimed[1])
            run_job(*claimed)
        elif once:
            return
        else:
            time.sleep(poll_seconds)
