"""Kept for convenience: same as `relecto gate`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relecto.quality import run_gate  # noqa: E402

sys.exit(run_gate(update_baseline="--update-baseline" in sys.argv))
