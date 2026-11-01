"""Checks that compare the report text with what the image analysis found.

IMG-001  nodule found on the images, not mentioned in Résultats (any side)          warning
IMG-002  nodule mentioned on the OPPOSITE side to the one the images show            critical
         -> the image geometry is the reference: the fix moves the side word to the
            image side, and text-only laterality fixes that contradict the image are
            withdrawn (see reconcile_with_images).
"""

from typing import List

from ..checker.content import get_content
from ..checker.extract import extract_mentions
from ..checker.issues import Evidence, Issue, Suggestion
from ..checker.parser import parse_report
from ..checker.rules import _side_word
from .findings import NoduleFinding

SIDE_FR = {"R": "droit", "L": "gauche"}
NODULE_KEYS = {"nodule", "masse"}


def _mentions(text):
    report = parse_report(text)
    return [m for m in extract_mentions(report, get_content().lexicon)
            if m.finding.key in NODULE_KEYS and m.affirmed and m.section in ("resultats", "conclusion")]


def _mm(meas) -> float:
    return meas.value * 10 if meas.unit == "cm" else meas.value


def _same_size(m, f: NoduleFinding) -> bool:
    """A wrong-side claim needs the written size to match the measured one (otherwise the
    text may describe another nodule the model missed: that is IMG-001's job)."""
    if not m.measurements:
        return False
    return any(abs(_mm(x) - f.long_axis_mm) <= max(3.0, 0.3 * f.long_axis_mm) for x in m.measurements)


def image_vs_report(text: str, findings: List[NoduleFinding], min_mm: float = 4.0) -> List[Issue]:
    mentions = _mentions(text)
    results = [m for m in mentions if m.section == "resultats"]
    issues = []
    sided = [f for f in findings if f.long_axis_mm >= min_mm]
    image_sides = {f.side for f in sided}
    for f in sided:
        if any(m.side in (f.side, "B", None) for m in results):
            continue
        opposite = [m for m in mentions if m.side in ("R", "L") and m.side != f.side and m.side not in image_sides
                    and _same_size(m, f)]
        where = f"{f.location_fr}, coupe {f.key_slice + 1}"
        if opposite:
            m = opposite[0]
            issues.append(Issue(
                "IMG-002", "critical", "Côté contredit par l'image",
                f"Compte rendu : {m.finding.label_fr} côté {SIDE_FR[m.side]}. Image : {f.finding_id} "
                f"({f.long_axis_mm:.0f} mm) côté {SIDE_FR[f.side]} ({where}).",
                "Vérifiez le côté sur l'image. La correction proposée reprend le côté mesuré sur l'image.",
                evidence=[Evidence(m.section, m.side_start or m.start, m.side_end or m.end, m.text),
                          Evidence("image", 0, 0, f"{f.finding_id} · {where} · {f.source}")],
                suggestion=Suggestion(m.side_start, m.side_end, m.side_text,
                                      _side_word(f.side, m.finding.gender, m.side_text))
                if m.side_start is not None else None,
            ))
            # every other wrong-side mention of the same finding gets the same fix
            for m2 in opposite[1:]:
                if m2.side_start is not None:
                    issues.append(Issue(
                        "IMG-002", "critical", "Côté contredit par l'image",
                        f"Même erreur de côté ({m2.section}) : l'image montre {f.finding_id} côté {SIDE_FR[f.side]}.",
                        "Vérifiez le côté sur l'image.",
                        evidence=[Evidence(m2.section, m2.side_start, m2.side_end, m2.text)],
                        suggestion=Suggestion(m2.side_start, m2.side_end, m2.side_text,
                                              _side_word(f.side, m2.finding.gender, m2.side_text))))
            continue
        issues.append(Issue(
            "IMG-001", "warning", "Image ↔ compte rendu",
            f"L'analyse d'image a détecté {f.finding_id} : nodule de {f.long_axis_mm:.0f} mm, poumon "
            f"{SIDE_FR[f.side]} ({f.location_fr}), non mentionné dans les résultats.",
            "Vérifiez l'image (coupe indiquée). Si vous confirmez, ajoutez-le aux résultats ; sinon rejetez.",
            evidence=[Evidence("resultats", 0, 0, f"{f.finding_id} · coupe {f.key_slice + 1} · {f.source}")],
        ))
    return issues


def reconcile_with_images(text_issues: List[Issue], image_issues: List[Issue], findings: List[NoduleFinding]) -> List[Issue]:
    """Text-only laterality fixes copy the side written in Résultats. When the images show the
    other side, that fix would spread the error, so it is withdrawn and the image issue leads."""
    image_sides = {f.side for f in findings}
    if not image_sides:
        return text_issues
    out = []
    for i in text_issues:
        if i.rule_id == "LAT-001" and i.suggestion and len(image_sides) == 1:
            side = next(iter(image_sides))
            target = i.suggestion.replacement.lower()
            if (side == "L" and target.startswith("droit")) or (side == "R" and target.startswith("gauche")):
                i.suggestion = None
                i.message = "Côtés discordants. L'image montre le côté " + SIDE_FR[side] + " : voir le contrôle image (IMG-002)."
        out.append(i)
    return out
