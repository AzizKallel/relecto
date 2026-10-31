"""Database access. SQLite for development, PostgreSQL in production:
set RELECTO_DATABASE_URL, e.g. postgresql+psycopg://relecto:...@db:5432/relecto
"""

import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import AuditEvent, Base, CheckRun, Centre, Decision, Report, User

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


def database_url() -> str:
    url = os.environ.get("RELECTO_DATABASE_URL")
    if url:
        return url
    path = REPO_DIR / "data" / "private" / "relecto.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


@lru_cache(maxsize=4)
def get_engine(url: str | None = None):
    url = url or database_url()
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    return create_engine(url, **kwargs)


def migrate(url: str | None = None) -> None:
    """Bring the database schema to the latest version (Alembic)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url or database_url())
    command.upgrade(cfg, "head")


@contextmanager
def session_scope(url: str | None = None):
    factory = sessionmaker(bind=get_engine(url or database_url()), expire_on_commit=False)
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def audit(session: Session, action: str, object_type: str | None = None, object_id: str | None = None,
          user_id: str | None = None, centre_id: str | None = None, **details) -> None:
    session.add(AuditEvent(action=action, object_type=object_type, object_id=object_id,
                           user_id=user_id, centre_id=centre_id, details=details or None))


__all__ = ["Base", "Centre", "User", "Report", "CheckRun", "Decision", "AuditEvent",
           "database_url", "get_engine", "migrate", "session_scope", "audit"]
