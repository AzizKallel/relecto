"""Issue objects returned to the UI / API."""

from dataclasses import asdict, dataclass, field
from typing import List, Optional

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Evidence:
    section: str
    start: int
    end: int
    text: str = ""


@dataclass
class Suggestion:
    start: Optional[int]
    end: Optional[int]
    original: Optional[str]
    replacement: str
    mode: str = "replace"   # "replace" a span, or "append_conclusion"


@dataclass
class Issue:
    rule_id: str
    severity: str
    category: str
    title: str
    message: str
    evidence: List[Evidence] = field(default_factory=list)
    suggestion: Optional[Suggestion] = None
    source: str = "rule"      # "rule" or "llm"

    def to_dict(self) -> dict:
        return asdict(self)
