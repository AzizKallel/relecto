"""End-to-end: CT study folder -> AI findings -> draft report -> checks -> images and viewer data.

Finding sources:
  "ai"         nodule detection + lobe segmentation models (imaging/ai.py)
  "reference"  radiologists' outlines stored with the study as DICOM SEG (public datasets, QA)
  "auto"       AI when the models are installed, otherwise reference outlines if present

When both are available the reference outlines are also measured and kept in the analysis
summary, so the AI can be scored against them (relecto evaluate).
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from .. import __version__
from ..checker import run_check
from .consistency import image_vs_report
from .draft import PLACEHOLDER, draft_ct_chest
from .findings import NoduleFinding, findings_from_mask, findings_from_reader_annotations
from .render import render_finding
from .seg import load_segmentations
from .volume import CTVolume, load_ct


@dataclass
class StudyAnalysis:
    ct: CTVolume
    findings: List[NoduleFinding]
    draft: str
    issues: list
    images: List[Path]
    seconds: float
    source: str = "reference"
    versions: dict = field(default_factory=dict)
    reference: List[NoduleFinding] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def summary(self) -> dict:
        ct = self.ct
        return {
            "source": self.source, "versions": self.versions, "seconds": self.seconds, "timings": self.timings,
            "notes": self.notes, "qc": ct.qc, "meta": ct.meta,
            "shape": list(ct.hu.shape), "spacing_mm": list(ct.spacing),
            "flip_lr": bool(ct.row_dir[0] < 0),
            "k_superior": bool(ct.normal[2] > 0),
            "reference_findings": [f.to_dict() for f in self.reference],
        }


def analyze_study(folder, out_dir: Optional[Path] = None, source: str = "auto",
                  indication: Optional[str] = None, progress: Callable[[str], None] = lambda t: None) -> StudyAnalysis:
    from . import ai

    t0 = time.time()
    timings, notes = {}, []
    progress("Chargement du scanner")
    ct = load_ct(folder)
    timings["load"] = round(time.time() - t0, 1)

    reference_anns = load_segmentations(folder, ct)
    ref_pairs = findings_from_reader_annotations(reference_anns, ct, with_masks=True) if reference_anns else []
    reference = [f for f, _ in ref_pairs]

    use_ai = source == "ai" or (source == "auto" and ai.available())
    if source == "ai" and not ai.available():
        raise RuntimeError("Les modèles d'IA ne sont pas installés sur ce poste (pip install TotalSegmentator).")
    versions = {"engine": __version__}
    lobes = None
    nodule_labels = np.zeros(ct.hu.shape, np.uint8)

    if use_ai:
        versions.update(ai.versions())
        t = time.time()
        progress("IA : segmentation des lobes pulmonaires")
        lobes = ai.segment_lobes(ct)
        timings["lobes"] = round(time.time() - t, 1)
        t = time.time()
        progress("IA : détection des nodules")
        mask = ai.detect_nodules(ct)
        timings["nodules"] = round(time.time() - t, 1)
        pairs = findings_from_mask(mask, ct, f"totalsegmentator-lung_nodules {versions.get('totalsegmentator', '')}".strip())
        findings = []
        for n, (f, m) in enumerate(pairs, 1):
            code = ai.lobe_at(lobes, ct, m)
            if code:
                _, name, lobe_side = ai.LOBES[code]
                if lobe_side == f.side:
                    f.lobe = name
                else:
                    notes.append(f"{f.finding_id} : côté géométrique et lobe en désaccord, localisation par tiers conservée.")
            nodule_labels[m] = n
            findings.append(f)
        used = "ai"
    elif reference:
        findings, used = reference, "reference"
        for n, (_, m) in enumerate(ref_pairs, 1):
            nodule_labels[m] = n
        notes.append("Résultats issus des contours de référence des radiologues (pas de modèle d'IA installé).")
    else:
        findings, used = [], "none"
        notes.append("Aucun modèle d'IA installé et aucune annotation : pas d'analyse automatique des nodules.")

    if used == "none":
        draft = draft_ct_chest(ct, [], indication or PLACEHOLDER).replace(
            "Pas de nodule pulmonaire détecté par l'analyse automatique (à confirmer par le radiologue).",
            f"Parenchyme pulmonaire : {PLACEHOLDER}")
    else:
        draft = draft_ct_chest(ct, findings, indication or PLACEHOLDER)
    progress("Vérification du brouillon")
    result = run_check(draft, "ct_chest", max_issues=50)
    versions.update({f"checker_{k}": v for k, v in result.versions.items()})
    issues = result.issues + image_vs_report(draft, findings)

    images = []
    if out_dir:
        progress("Préparation de la visionneuse")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        np.save(out_dir / "volume.npy", ct.hu)
        np.save(out_dir / "nodules.npy", nodule_labels)
        if lobes is not None:
            np.save(out_dir / "lobes.npy", lobes)
        for f in findings:
            images.append(render_finding(ct, f, out_dir / f"{f.finding_id}.png"))
        (out_dir / "draft_report.txt").write_text(draft, encoding="utf-8")
    a = StudyAnalysis(ct, findings, draft, issues, images, round(time.time() - t0, 1), used, versions,
                      reference, timings, notes)
    if out_dir:
        (Path(out_dir) / "findings.json").write_text(json.dumps(
            {**a.summary(), "findings": [f.to_dict() for f in findings], "issues": [i.to_dict() for i in issues]},
            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return a
