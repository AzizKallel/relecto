"""Accent- and case-insensitive text with a map back to the original offsets.

Matching runs on the normalized text; every span we report is mapped back to
the radiologist's original text so the UI can highlight the exact words.
"""

import unicodedata
from dataclasses import dataclass
from typing import List

_SPECIAL = {
    "œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae",
    "’": "'", "‘": "'", "`": "'", "´": "'",
    "×": "x", " ": " ", " ": " ",
}


@dataclass
class NormText:
    original: str
    norm: str
    idx: List[int]  # idx[i] = index in original of norm[i]

    def orig_span(self, start: int, end: int) -> tuple:
        """Map a [start, end) span of the normalized text to the original."""
        if start >= len(self.idx):
            return len(self.original), len(self.original)
        o_start = self.idx[start]
        o_end = self.idx[end - 1] + 1 if end > start else o_start
        return o_start, o_end


def normalize(text: str) -> NormText:
    out, idx = [], []
    for i, ch in enumerate(text):
        if ch in _SPECIAL:
            rep = _SPECIAL[ch]
        else:
            decomposed = unicodedata.normalize("NFD", ch)
            rep = "".join(c for c in decomposed if not unicodedata.combining(c))
        for r in rep.lower():
            out.append(r)
            idx.append(i)
    return NormText(text, "".join(out), idx)
