"""French CT chest draft report from structured findings.

The draft states only what was measured. Everything the image analysis did not
assess is left as an explicit placeholder for the radiologist — never filled
with assumed normal findings.
"""

from typing import List

from .findings import NoduleFinding
from .volume import CTVolume

SIDE_FR = {"R": "droit", "L": "gauche"}
PLACEHOLDER = "[à compléter par le radiologue]"


def _noun(f: NoduleFinding) -> str:
    return "Masse pulmonaire" if f.long_axis_mm > 30 else "Nodule pulmonaire"


def _fmt(x: float) -> str:
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}".replace(".", ",")


def draft_ct_chest(ct: CTVolume, findings: List[NoduleFinding], indication: str = PLACEHOLDER) -> str:
    contrast = ("avec injection de produit de contraste iodé" if ct.meta.get("contrast")
                else "sans injection de produit de contraste")
    th = ct.meta.get("slice_thickness_mm") or ct.spacing[0]
    lines = [
        f"INDICATION : {indication}",
        "",
        f"TECHNIQUE : Acquisition hélicoïdale thoracique {contrast}. Reconstructions en coupes de {_fmt(th)} mm.",
        "",
        "RÉSULTATS :",
    ]
    if findings:
        for f in findings:
            lines.append(
                f"{_noun(f)} du tiers {f.zone} du poumon {SIDE_FR[f.side]}, mesurant {_fmt(f.long_axis_mm)} x "
                f"{_fmt(f.short_axis_mm)} mm (densité moyenne {f.mean_hu:.0f} UH).")
    else:
        lines.append("Pas de nodule pulmonaire détecté par l'analyse automatique.")
    lines += [f"Médiastin, plèvre, paroi et autres structures : {PLACEHOLDER}", "", "CONCLUSION :"]
    for f in findings:
        lines.append(f"{_noun(f)} {SIDE_FR[f.side]} de {_fmt(f.long_axis_mm)} mm (tiers {f.zone}).")
    lines.append(f"Conclusion et conduite à tenir : {PLACEHOLDER}")
    return "\n".join(lines) + "\n"
