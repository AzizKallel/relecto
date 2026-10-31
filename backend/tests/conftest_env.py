"""Shared test setup: a throw-away database for every test run."""
import os
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="relecto-test-")
os.environ.setdefault("RELECTO_DATABASE_URL", f"sqlite:///{Path(TMP) / 'test.db'}")
REPO = Path(__file__).resolve().parents[2]
DEMO = (REPO / "clinical-content" / "examples" / "demo_ct_chest.txt").read_text(encoding="utf-8")
