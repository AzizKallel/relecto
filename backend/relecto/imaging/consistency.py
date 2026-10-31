"""IMG-001: findings seen on the images but not mentioned in the report text."""

from typing import List

from ..checker.content import get_content
from ..checker.extract import extract_mentions
from ..checker.issues import Evidence, Issue
from ..checker.parser import parse_report
from .findings import NoduleFinding

SIDE_FR = {"R": "droit", "L": "gauche"}
NODULE_KEYS = {"nodule", "masse"}


def image_vs_report(text: str, findings: List[NoduleFinding], min_mm: float = 4.0) -> List[Issue]:
    report = parse_report(text)
    mentions = [m for m in extract_mentions(report, get_content().lexicon)
                if m.finding.key in NODULE_KEYS and m.affirmed and m.section == "resultats"]
    issues = []
    for f in findings:
        if f.long_axis_mm < min_mm:
            continue
        same_side = [m for m in mentions if m.side in (f.side, "B", None)]
        if same_side:
            continue
        issues.append(Issue(
            "IMG-001", "warning", "Image ↔ compte rendu",
            f"L'analyse d'image a détecté {f.finding_id} : nodule de {f.long_axis_mm:.0f} mm, poumon "
            f"{SIDE_FR[f.side]} (tiers {f.zone}), non mentionné dans les résultats.",
            "Vérifiez l'image (coupe indiquée). Si vous confirmez, ajoutez-le aux résultats ; sinon rejetez.",
            evidence=[Evidence("resultats", 0, 0, f"{f.finding_id} · coupe {f.key_slice + 1} · {f.source}")],
        ))
    return issues
