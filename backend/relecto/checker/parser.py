"""Split a French radiology report into sections and sentences."""

import re
from dataclasses import dataclass, field
from typing import Dict, List

from .normalize import normalize

# Canonical section -> header patterns (normalized text: no accents, lower case)
SECTION_HEADERS = {
    "indication": r"indications?|renseignements? cliniques?|motif|contexte",
    "technique": r"techniques?|protocole|acquisition",
    "resultats": r"resultats?|description|analyse|constatations?|compte[- ]rendu",
    "conclusion": r"(?:en )?conclusions?|synthese|impression|au total",
}

_HEADER_RE = re.compile(
    r"^[ \t]*(?:#+\s*)?(?P<h>" + "|".join(f"(?P<{k}>{v})" for k, v in SECTION_HEADERS.items())
    + r")[ \t]*(?:[:\-–][ \t]*|$)",
    re.MULTILINE,
)

_SENT_END = re.compile(r"(?<=[.!?;])\s+|\n+")


@dataclass
class Sentence:
    section: str
    text: str
    start: int  # offset in the original report
    end: int


@dataclass
class ParsedReport:
    text: str
    sections: Dict[str, str] = field(default_factory=dict)
    section_spans: Dict[str, tuple] = field(default_factory=dict)
    sentences: List[Sentence] = field(default_factory=list)

    def sentences_in(self, section: str) -> List[Sentence]:
        return [s for s in self.sentences if s.section == section]


def parse_report(text: str) -> ParsedReport:
    nt = normalize(text)
    report = ParsedReport(text=text)

    headers = []
    for m in _HEADER_RE.finditer(nt.norm):
        name = next(k for k in SECTION_HEADERS if m.group(k))
        o_start, _ = nt.orig_span(m.start(), m.end())
        content_start = nt.idx[m.end()] if m.end() < len(nt.idx) else len(text)
        headers.append((name, o_start, content_start))

    if not headers:
        # No recognizable headers: treat the whole text as findings.
        blocks = [("resultats", 0, len(text))]
    else:
        blocks = []
        for i, (name, _, c_start) in enumerate(headers):
            c_end = headers[i + 1][1] if i + 1 < len(headers) else len(text)
            blocks.append((name, c_start, c_end))

    for name, c_start, c_end in blocks:
        # First occurrence wins for the span; content is concatenated.
        report.section_spans.setdefault(name, (c_start, c_end))
        chunk = text[c_start:c_end]
        report.sections[name] = (report.sections.get(name, "") + "\n" + chunk).strip()
        pos = 0
        for piece in _SENT_END.split(chunk):
            if not piece:
                continue
            local = chunk.find(piece, pos)
            pos = local + len(piece)
            stripped = piece.strip(" \t-•*")
            if len(stripped) < 2:
                continue
            offset = c_start + local + piece.find(stripped)
            report.sentences.append(Sentence(name, stripped, offset, offset + len(stripped)))
    return report
