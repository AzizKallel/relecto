"""Report checker: deterministic rules driven by clinical-content/ YAML."""

from .engine import CheckResult, apply_suggestion, check_report, run_check
from .issues import Evidence, Issue, Suggestion

__all__ = ["run_check", "check_report", "apply_suggestion", "CheckResult", "Issue", "Evidence", "Suggestion"]
