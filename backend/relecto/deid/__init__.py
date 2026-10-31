"""De-identification of French radiology reports, BEFORE any check or model.

Rules-based, conservative: it prefers masking too much over too little.
It replaces names, dates, identifiers, phone numbers, e-mails and addresses
with placeholders such as [NOM] or [DATE]. The mapping back to the original
values is returned to the caller and must stay on the centre's machine; the
API never logs it.

This is NOT a certified anonymisation tool. Validate it on real samples
(see tests/test_deid.py) before any pilot, and keep a human check.
"""

import re
from dataclasses import dataclass, field
from typing import List

_MONTHS = (r"janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|"
           r"octobre|novembre|decembre|décembre")
_CAP = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+"
_UPPER = r"[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ'\-]{1,}"
_NAME = rf"(?:{_CAP}|{_UPPER})(?:[ \t]+(?:{_CAP}|{_UPPER})){{0,3}}"

# (placeholder, compiled pattern, group holding the value to mask or 0)
PATTERNS = [
    ("[EMAIL]", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), 0),
    ("[MEDECIN]", re.compile(rf"\b(?:Dr|Docteur|Pr|Professeur)\.?[ \t]+(?P<v>{_NAME})"), "v"),
    ("[NOM]", re.compile(rf"\b(?:M\.|Mr\.?|Monsieur|Mme\.?|Madame|Mlle\.?|Mademoiselle)[ \t]+(?P<v>{_NAME})"), "v"),
    ("[NOM]", re.compile(rf"(?im)^[ \t]*(?:nom(?: et pr[ée]nom)?|pr[ée]nom|nom de naissance|identit[ée])[ \t]*:[ \t]*(?P<v>[^\n]+)"), "v"),
    ("[NOM]", re.compile(rf"(?i:\b(?:patient(?:e)?|nom|pr[ée]nom))[ \t]*:[ \t]*(?P<v>{_NAME})"), "v"),
    ("[ID]", re.compile(r"(?i)\b(?:IPP|NDA|CIN|matricule|n°[ \t]*(?:de[ \t]+)?(?:dossier|patient|examen|cnam|cin)|dossier|identifiant|ID)[ \t]*(?:n°|no|num[ée]ro)?[ \t]*[:#]?[ \t]*(?P<v>[A-Z0-9][A-Z0-9\-/]{3,})"), "v"),
    ("[DATE_NAISSANCE]", re.compile(rf"(?i)\bn[ée]e?[ \t]+le[ \t]+(?P<v>\d{{1,2}}[/.\-]\d{{1,2}}[/.\-]\d{{2,4}}|\d{{1,2}}[ \t]+(?:{_MONTHS})[ \t]+\d{{4}})"), "v"),
    ("[DATE]", re.compile(rf"(?i)\b(?:\d{{1,2}}[/.\-]\d{{1,2}}[/.\-]\d{{2,4}}|\d{{1,2}}(?:er)?[ \t]+(?:{_MONTHS})[ \t]+\d{{4}})\b"), 0),
    ("[TEL]", re.compile(r"(?:\+|00)216[ \t.]?\d{2}[ \t.]?\d{3}[ \t.]?\d{3}|\b\d{2}[ \t.]\d{3}[ \t.]\d{3}\b"), 0),
    ("[ADRESSE]", re.compile(r"(?i)\b\d{0,4}[ \t,]*(?:rue|avenue|av\.|boulevard|bd|cit[ée]|r[ée]sidence|route|impasse|lotissement)[ \t]+[^\n,;]{2,60}"), 0),
    ("[ID]", re.compile(r"\b\d{8,}\b"), 0),  # long digit runs: CIN (8 digits), file numbers
]


@dataclass
class Replacement:
    kind: str
    original: str
    start: int   # offsets in the ORIGINAL text
    end: int


@dataclass
class DeidResult:
    text: str
    replacements: List[Replacement] = field(default_factory=list)

    def counts(self) -> dict:
        out = {}
        for r in self.replacements:
            out[r.kind] = out.get(r.kind, 0) + 1
        return out


def deidentify(text: str) -> DeidResult:
    spans = []  # (start, end, placeholder)
    for placeholder, rx, group in PATTERNS:
        for m in rx.finditer(text):
            s, e = (m.start(group), m.end(group)) if group else (m.start(), m.end())
            value = text[s:e].rstrip(" \t.,;")
            e = s + len(value)
            if e - s < 2:
                continue
            if any(s < pe and ps < e for ps, pe, _ in spans):
                continue  # overlaps an earlier, more specific match
            spans.append((s, e, placeholder))
    spans.sort()
    out, reps, pos = [], [], 0
    for s, e, ph in spans:
        out.append(text[pos:s])
        out.append(ph)
        reps.append(Replacement(ph, text[s:e], s, e))
        pos = e
    out.append(text[pos:])
    return DeidResult("".join(out), reps)


def reidentify(text: str, result: DeidResult) -> str:
    """Put the original values back, in order (local use only)."""
    out = text
    for r in result.replacements:
        out = out.replace(r.kind, r.original, 1)
    return out
