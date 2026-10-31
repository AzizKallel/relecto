"""Deterministic rules. Each rule returns Issues that cite spans of the report.

Rule IDs are stable so radiologists can discuss, tune and audit them. Whether
a rule runs, and its severity, is decided by the exam's rule pack
(clinical-content/rule-packs/*.yaml), not here.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import List

from .content import Lexicon, RulePack
from .extract import Mention
from .issues import Evidence, Issue, Suggestion
from .normalize import normalize
from .parser import ParsedReport

SIDE_FR = {"R": "droit", "L": "gauche", "B": "bilatéral"}
SECTION_LABELS = {"indication": "Indication", "technique": "Technique",
                  "resultats": "Résultats", "conclusion": "Conclusion"}


@dataclass
class Ctx:
    report: ParsedReport
    pack: RulePack
    lex: Lexicon
    mentions: List[Mention]


def _ev(m: Mention, use_side: bool = False) -> Evidence:
    if use_side and m.side_start is not None:
        return Evidence(m.section, min(m.start, m.side_start), max(m.end, m.side_end))
    return Evidence(m.section, m.sentence.start, m.sentence.end)


def _side_word(side: str, gender: str, original: str) -> str:
    """Inflect the corrected side word like the word it replaces."""
    plural = original.lower().endswith("s")
    if side == "L":
        word = "gauche"
    else:
        word = "droite" if gender == "f" else "droit"
        if original.lower().startswith("droit"):
            word = original.lower().rstrip("s")  # keep the radiologist's own gender
    if plural:
        word += "s"
    return word.capitalize() if original[:1].isupper() else word


def rule_sections(c: Ctx) -> List[Issue]:
    return [Issue("SEC-001", "warning", "Section manquante",
                  f"Section « {SECTION_LABELS.get(sec, sec)} » absente ou vide.",
                  "Cette section est attendue pour ce type d'examen.")
            for sec in c.pack.required_sections if not c.report.sections.get(sec, "").strip()]


def rule_technique(c: Ctx) -> List[Issue]:
    req = c.pack.technique
    tech = c.report.sections.get("technique", "")
    if not req or not tech:
        return []
    norm = normalize(tech).norm
    if any(re.search(t, norm) for t in req["any_of"]):
        return []
    start, end = c.report.section_spans["technique"]
    return [Issue("TECH-001", "warning", "Technique incomplète", req.get("message", "Technique incomplète."),
                  "Élément attendu dans la section Technique.", evidence=[Evidence("technique", start, end)])]


def rule_laterality(c: Ctx) -> List[Issue]:
    """Same finding affirmed with one side in Résultats and the other in Conclusion."""
    issues = []
    by_key = defaultdict(lambda: {"resultats": [], "conclusion": []})
    for m in c.mentions:
        if m.affirmed and m.side in ("R", "L") and m.section in ("resultats", "conclusion"):
            by_key[m.finding.key][m.section].append(m)
    for d in by_key.values():
        res_sides = {m.side for m in d["resultats"]}
        if len(res_sides) != 1:
            continue  # absent, or on both sides: ambiguous, skip
        res_side = next(iter(res_sides))
        for cm in d["conclusion"]:
            if cm.side == res_side:
                continue
            r = d["resultats"][0]
            issues.append(Issue(
                "LAT-001", "critical", "Latéralité",
                f"{cm.finding.label_fr.capitalize()} : côté {SIDE_FR[res_side]} dans les résultats, "
                f"{SIDE_FR[cm.side]} dans la conclusion.",
                "Vérifiez le côté. La correction proposée reprend le côté des résultats.",
                evidence=[_ev(r, True), _ev(cm, True)],
                suggestion=Suggestion(cm.side_start, cm.side_end, cm.side_text,
                                      _side_word(res_side, cm.finding.gender, cm.side_text)),
            ))
    return issues


def rule_implied_side(c: Ctx) -> List[Issue]:
    """Anatomy that exists on one side only, described on the other side."""
    found = {}
    for sent in c.report.sentences:
        nt = normalize(sent.text)
        for rx, side, why in c.lex.implied_side:
            for m in rx.finditer(nt.norm):
                window = nt.norm[m.end(): m.end() + 25]
                w = re.search(r"\bgauches?\b" if side == "R" else r"\bdroite?s?\b", window)
                if not w:
                    continue
                s, _ = nt.orig_span(m.start(), m.end())
                _, e = nt.orig_span(m.end() + w.start(), m.end() + w.end())
                ev = Evidence(sent.section, sent.start + s, sent.start + e)
                if rx.pattern in found:
                    found[rx.pattern].evidence.append(ev)
                else:
                    found[rx.pattern] = Issue("ANAT-001", "critical", "Anatomie",
                                              f"« {sent.text[s:e]} » : localisation anatomiquement impossible.",
                                              why, evidence=[ev])
    return list(found.values())


def rule_negation_conflict(c: Ctx) -> List[Issue]:
    """Same finding both denied and described in the same section."""
    issues = []
    for section in ("resultats", "conclusion"):
        groups = defaultdict(lambda: {"neg": [], "pos": []})
        for m in c.mentions:
            if m.section == section:
                groups[m.finding.key]["neg" if m.negated else "pos"].append(m)
        for g in groups.values():
            for n in g["neg"]:
                conflicting = [p for p in g["pos"]
                               if n.side is None or p.side is None or n.side == p.side or "B" in (n.side, p.side)]
                if conflicting:
                    issues.append(Issue(
                        "NEG-001", "critical", "Contradiction",
                        f"{n.finding.label_fr.capitalize()} à la fois nié et décrit ({SECTION_LABELS[section]}).",
                        "Une des deux phrases est probablement une erreur de dictée. Laquelle est correcte ?",
                        evidence=[_ev(n), _ev(conflicting[0])],
                    ))
                    break
    return issues


def rule_missing_size(c: Ctx) -> List[Issue]:
    return [Issue("MEAS-001", "warning", "Mesure manquante",
                  f"{m.finding.label_fr.capitalize()} décrit(e) sans mesure.",
                  "Ajoutez la taille (mm). Relecto ne propose jamais de valeur.", evidence=[_ev(m)])
            for m in c.mentions
            if m.section == "resultats" and m.affirmed and m.finding.requires_size and not m.measurements]


def rule_measure_plausibility(c: Ctx) -> List[Issue]:
    issues, seen = [], set()
    for m in c.mentions:
        for meas in m.measurements:
            if (meas.start, meas.end) in seen:
                continue
            seen.add((meas.start, meas.end))
            if meas.mm > 300:
                issues.append(Issue("MEAS-002", "warning", "Mesure", f"Mesure inhabituelle : « {meas.text} ».",
                                    "Vérifiez la valeur et l'unité (mm / cm).",
                                    evidence=[Evidence(m.section, meas.start, meas.end)]))
            elif m.finding.key == "nodule" and meas.mm > 30 and m.section == "resultats":
                issues.append(Issue("TERM-001", "info", "Terminologie",
                                    f"« {m.text} » de {meas.text} : au-delà de 3 cm, on parle généralement de masse.",
                                    "Convention terminologique usuelle (glossaire de la Fleischner Society) — à confirmer.",
                                    evidence=[_ev(m)]))
    return issues


def rule_conclusion_coverage(c: Ctx) -> List[Issue]:
    if not c.report.sections.get("conclusion", "").strip():
        return []
    issues = []
    res_pos, res_neg, conc_pos = defaultdict(list), defaultdict(list), defaultdict(list)
    for m in c.mentions:
        if m.section == "resultats":
            (res_neg if m.negated else res_pos)[m.finding.key].append(m)
        elif m.section == "conclusion" and m.affirmed:
            conc_pos[m.finding.key].append(m)

    for key, ms in res_pos.items():
        if ms[0].finding.significant and key not in conc_pos:
            m = ms[0]
            issues.append(Issue(
                "CONC-001", "warning", "Résultats ↔ conclusion",
                f"{m.finding.label_fr.capitalize()} décrit(e) dans les résultats mais absent(e) de la conclusion.",
                "Proposition : reprendre ce résultat dans la conclusion, tel qu'il est écrit.",
                evidence=[_ev(m)],
                suggestion=Suggestion(None, None, None, m.sentence.text, mode="append_conclusion"),
            ))
    for key, ms in conc_pos.items():
        if key in res_pos:
            continue
        cm = ms[0]
        if res_neg.get(key):
            issues.append(Issue("CONC-002", "critical", "Résultats ↔ conclusion",
                                f"La conclusion mentionne {cm.finding.label_fr}, que les résultats excluent.",
                                "La conclusion contredit les résultats.",
                                evidence=[_ev(res_neg[key][0]), _ev(cm)]))
        elif c.report.sections.get("resultats"):
            issues.append(Issue("CONC-003", "info", "Résultats ↔ conclusion",
                                f"{cm.finding.label_fr.capitalize()} en conclusion sans description dans les résultats.",
                                "Vérifiez que ce résultat est décrit dans les résultats.", evidence=[_ev(cm)]))
    return issues


def rule_modality_vocabulary(c: Ctx) -> List[Issue]:
    """Vocabulary of another modality (e.g. MRI signal words in a CT report)."""
    cfg = c.pack.config("MOD-001")
    forbidden = [re.compile(r"\b(?:" + p + r")\b") for p in cfg.get("forbidden", [])]
    ignore = [re.compile(p) for p in cfg.get("ignore_sentences_with", [])]
    if not forbidden:
        return []
    evidence, words = [], []
    for sent in c.report.sentences:
        if sent.section == "indication":
            continue  # clinical history may legitimately mention other exams
        nt = normalize(sent.text)
        if any(rx.search(nt.norm) for rx in ignore):
            continue
        for rx in forbidden:
            for m in rx.finditer(nt.norm):
                s, e = nt.orig_span(m.start(), m.end())
                evidence.append(Evidence(sent.section, sent.start + s, sent.start + e))
                words.append(sent.text[s:e])
    if not evidence:
        return []
    shown = ", ".join(f"« {w} »" for w in dict.fromkeys(words))
    return [Issue("MOD-001", "warning", "Vocabulaire de modalité", f"Termes inattendus pour cet examen : {shown}.",
                  cfg.get("message", "Vérifiez le vocabulaire."), evidence=evidence[:6])]


ALL_RULES = {
    "SEC-001": rule_sections,
    "TECH-001": rule_technique,
    "LAT-001": rule_laterality,
    "ANAT-001": rule_implied_side,
    "NEG-001": rule_negation_conflict,
    "MEAS-001": rule_missing_size,
    "MEAS-002": rule_measure_plausibility,   # also emits TERM-001
    "CONC-001": rule_conclusion_coverage,    # also emits CONC-002 / CONC-003
    "MOD-001": rule_modality_vocabulary,
}
