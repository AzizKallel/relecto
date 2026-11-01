"""Database models. Only de-identified report text is stored; never the
re-identification map, never images.

The audit table is append-only: the application never updates or deletes it.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Centre(Base):
    __tablename__ = "centres"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    centre_id: Mapped[str | None] = mapped_column(ForeignKey("centres.id"))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    # radiologist, junior, secretary, centre_admin, support
    role: Mapped[str] = mapped_column(String(32), default="radiologist")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(64))   # e.g. "Dr", printed on signed reports
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    centre_id: Mapped[str | None] = mapped_column(ForeignKey("centres.id"), index=True)
    study_id: Mapped[str | None] = mapped_column(ForeignKey("studies.id"), index=True)
    exam: Mapped[str] = mapped_column(String(64))
    draft_text: Mapped[str | None] = mapped_column(Text)   # AI draft as generated, kept to measure edits
    signed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    text_deid: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="checked")  # checked | signed
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_versions: Mapped[dict | None] = mapped_column(JSON)

    checks: Mapped[list["CheckRun"]] = relationship(back_populates="report", order_by="CheckRun.created_at")


class CheckRun(Base):
    __tablename__ = "check_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), index=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    versions: Mapped[dict] = mapped_column(JSON)
    issues: Mapped[list] = mapped_column(JSON)
    duration_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    report: Mapped[Report] = relationship(back_populates="checks")


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), index=True)
    check_id: Mapped[str | None] = mapped_column(ForeignKey("check_runs.id"))
    rule_id: Mapped[str] = mapped_column(String(32), index=True)
    decision: Mapped[str] = mapped_column(String(16))  # accepted | rejected
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    versions: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    user_id: Mapped[str | None] = mapped_column(String(32))
    centre_id: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str | None] = mapped_column(String(32))
    object_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)  # never report text


class Study(Base):
    """One imaging study received at the centre. Pixel data stays on disk at the edge node
    (storage_path); the database keeps only what the worklist and the report need."""
    __tablename__ = "studies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    centre_id: Mapped[str | None] = mapped_column(ForeignKey("centres.id"), index=True)
    study_uid: Mapped[str] = mapped_column(String(128), index=True)
    patient_id: Mapped[str | None] = mapped_column(String(64))
    patient_name: Mapped[str | None] = mapped_column(String(200))
    patient_sex: Mapped[str | None] = mapped_column(String(8))
    patient_birth_date: Mapped[str | None] = mapped_column(String(16))
    accession: Mapped[str | None] = mapped_column(String(64))
    modality: Mapped[str | None] = mapped_column(String(16))
    body_part: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(200))
    study_date: Mapped[str | None] = mapped_column(String(16))
    exam: Mapped[str] = mapped_column(String(64), default="ct_chest")
    indication: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(16), default="routine")    # routine | urgent
    # received -> queued -> analyzing -> ready -> reported -> signed ; failed
    status: Mapped[str] = mapped_column(String(16), default="received", index=True)
    storage_path: Mapped[str] = mapped_column(String(500))
    n_images: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="upload")    # upload | dicom | folder
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    analysis: Mapped[dict | None] = mapped_column(JSON)      # geometry, QC, model versions, timings
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    findings: Mapped[list["Finding"]] = relationship(back_populates="study", order_by="Finding.label",
                                                     cascade="all, delete-orphan")
    jobs: Mapped[list["Job"]] = relationship(back_populates="study", order_by="Job.created_at",
                                             cascade="all, delete-orphan")


class Finding(Base):
    """A finding proposed by the image analysis. The radiologist confirms or rejects it."""
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id"), index=True)
    label: Mapped[str] = mapped_column(String(16))              # N1, N2…
    kind: Mapped[str] = mapped_column(String(32), default="nodule")
    source: Mapped[str] = mapped_column(String(64))             # model name + version, or reference_readers
    data: Mapped[dict] = mapped_column(JSON)                    # measurements, location, key slice
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed | confirmed | rejected
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    study: Mapped[Study] = relationship(back_populates="findings")


class Job(Base):
    """Background work for the AI worker. The database is the queue: no extra broker to run."""
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="analyze")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|done|failed
    progress: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    worker: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    study: Mapped[Study] = relationship(back_populates="jobs")
