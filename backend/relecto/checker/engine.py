"""Entry point: run the enabled rules (and optionally the LLM) on one report."""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .. import __version__
from .content import get_content
from .extract import extract_mentions
from .issues import SEVERITY_ORDER, Issue
from .parser import parse_report
from .rules import ALL_RULES, Ctx


@dataclass
class CheckResult:
    issues: List[Issue]
    versions: Dict[str, str]
    ms: float
    all_issues: List[Issue] = field(default_factory=list)  # before the display cap


def run_check(text: str, exam: str = "ct_chest", use_llm: bool = False,
              llm_model: Optional[str] = None, max_issues: int = 8) -> CheckResult:
    t0 = time.perf_counter()
    content = get_content()
    pack = content.pack(exam)
    report = parse_report(text)
    ctx = Ctx(report, pack, content.lexicon, extract_mentions(report, content.lexicon))

    issues: List[Issue] = []
    for rule in dict.fromkeys(ALL_RULES.values()):  # each function once
        issues.extend(rule(ctx))

    if use_llm:
        from ..llm import llm_check
        issues.extend(llm_check(text, {"label": pack.label}, existing=issues, model=llm_model))

    # The rule pack decides what runs and how severe it is.
    kept, seen = [], set()
    for issue in issues:
        if not pack.enabled(issue.rule_id):
            continue
        issue.severity = pack.severity(issue.rule_id, issue.severity)
        for ev in issue.evidence:
            ev.text = text[ev.start:ev.end]
        key = (issue.rule_id, tuple((e.start, e.end) for e in issue.evidence))
        if key in seen:
            continue
        seen.add(key)
        kept.append(issue)

    kept.sort(key=lambda i: (SEVERITY_ORDER[i.severity], i.evidence[0].start if i.evidence else -1))
    versions = {"engine": __version__, "lexicon": content.lexicon.version,
                "rule_pack": f"{pack.exam}@{pack.version}"}
    return CheckResult(kept[:max_issues], versions, round((time.perf_counter() - t0) * 1000, 1), kept)


def check_report(text: str, exam: str = "ct_chest", use_llm: bool = False,
                 llm_model: Optional[str] = None, max_issues: int = 8) -> List[Issue]:
    return run_check(text, exam, use_llm, llm_model, max_issues).issues


def apply_suggestion(text: str, issue: Issue) -> str:
    """Return the report with one accepted suggestion applied."""
    s = issue.suggestion
    if s is None:
        return text
    if s.mode == "replace":
        if text[s.start:s.end] != s.original:
            raise ValueError("Report changed since the check; re-run it.")
        return text[:s.start] + s.replacement + text[s.end:]
    if s.mode == "append_conclusion":
        return text.rstrip() + "\n" + s.replacement + "\n"
    raise ValueError(f"Unknown suggestion mode {s.mode}")
