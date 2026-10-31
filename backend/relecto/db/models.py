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
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    centre_id: Mapped[str | None] = mapped_column(ForeignKey("centres.id"), index=True)
    exam: Mapped[str] = mapped_column(String(64))
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
