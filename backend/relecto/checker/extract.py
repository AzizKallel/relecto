"""Find findings, their side, negation and measurements in each sentence.

All vocabulary comes from the Lexicon (clinical-content/lexicon/fr.yaml).
"""

from dataclasses import dataclass
from typing import List, Optional

from .content import FindingType, Lexicon
from .normalize import normalize
from .parser import ParsedReport, Sentence


@dataclass
class Measurement:
    value: float
    unit: str
    start: int
    end: int
    text: str

    @property
    def mm(self) -> float:
        return self.value * 10 if self.unit.startswith("c") else self.value


@dataclass
class Mention:
    finding: FindingType
    section: str
    sentence: Sentence
    start: int
    end: int
    text: str
    negated: bool
    side: Optional[str]
    side_start: Optional[int] = None
    side_end: Optional[int] = None
    side_text: Optional[str] = None
    measurements: tuple = ()

    @property
    def affirmed(self) -> bool:
        return not self.negated


def _clause_bounds(lex: Lexicon, norm: str, pos: int):
    start = 0
    for m in lex.clause.finditer(norm, 0, pos):
        start = m.end()
    m = lex.clause.search(norm, pos)
    end = m.start() if m else len(norm)
    return start, end


def _is_negated(lex: Lexicon, norm: str, f_start: int, f_end: int) -> bool:
    c_start, c_end = _clause_bounds(lex, norm, f_start)
    if lex.neg_pre.search(norm[c_start:f_start]) or lex.neg_post.search(norm[f_end:c_end]):
        return True
    # Enumerations carry negation across commas:
    # "Pas d'épanchement pleural, de pneumothorax ni de condensation."
    seg_start, seg_end = c_start, f_start
    while seg_start > 0 and lex.enum_start.match(norm[seg_start:seg_end]):
        prev_end = seg_start - 1
        p_start, _ = _clause_bounds(lex, norm, prev_end)
        if p_start >= seg_start:
            break
        if lex.neg_pre.search(norm[p_start:prev_end]):
            return True
        seg_start, seg_end = p_start, prev_end
    return False


def _nearest_side(lex: Lexicon, norm: str, f_start: int, f_end: int, taken: set):
    """Closest side word in the same clause, preferring words after the finding."""
    c_start, c_end = _clause_bounds(lex, norm, f_start)
    best = None
    for side, rx in lex.side_res:
        for m in rx.finditer(norm, c_start, c_end):
            if (m.start(), m.end()) in taken:
                continue
            dist = m.start() - f_end if m.start() >= f_end else (f_start - m.end()) + 40
            if best is None or dist < best[0]:
                best = (dist, side, m.start(), m.end())
    return best


def extract_mentions(report: ParsedReport, lex: Lexicon) -> List[Mention]:
    mentions: List[Mention] = []
    for sent in report.sentences:
        nt = normalize(sent.text)
        norm = nt.norm
        measures = []
        for m in lex.measure.finditer(norm):
            s, e = nt.orig_span(m.start(), m.end())
            measures.append(Measurement(float(m.group("val").replace(",", ".")), m.group("unit"),
                                        sent.start + s, sent.start + e, sent.text[s:e]))
        hits = [(m.start(), m.end(), ft) for ft, rx in lex.finding_res for m in rx.finditer(norm)]
        hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
        kept = []
        for h in hits:
            if any(k[0] <= h[0] and h[1] <= k[1] for k in kept):
                continue
            kept.append(h)

        taken = set()
        for f_start, f_end, ftype in kept:
            negated = _is_negated(lex, norm, f_start, f_end)
            side = _nearest_side(lex, norm, f_start, f_end, taken)
            s, e = nt.orig_span(f_start, f_end)
            mention = Mention(ftype, sent.section, sent, sent.start + s, sent.start + e,
                              sent.text[s:e], negated, None, measurements=tuple(measures))
            if side:
                taken.add((side[2], side[3]))
                ss, se = nt.orig_span(side[2], side[3])
                mention.side = side[1]
                mention.side_start, mention.side_end = sent.start + ss, sent.start + se
                mention.side_text = sent.text[ss:se]
            mentions.append(mention)
    return mentions
