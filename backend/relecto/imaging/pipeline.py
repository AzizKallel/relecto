"""End-to-end: CT study folder -> findings -> draft report -> checks -> images."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..checker import run_check
from .consistency import image_vs_report
from .draft import draft_ct_chest
from .findings import NoduleFinding, findings_from_reader_annotations
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


def analyze_study(folder, out_dir: Optional[Path] = None, source: str = "reference") -> StudyAnalysis:
    t0 = time.time()
    ct = load_ct(folder)
    if source == "reference":
        findings = findings_from_reader_annotations(load_segmentations(folder, ct), ct)
    else:
        raise ValueError(f"Unknown finding source '{source}' (image AI models plug in here)")
    draft = draft_ct_chest(ct, findings)
    issues = run_check(draft, "ct_chest", max_issues=50).issues + image_vs_report(draft, findings)
    images = []
    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in findings:
            images.append(render_finding(ct, f, out_dir / f"{f.finding_id}.png"))
        (out_dir / "draft_report.txt").write_text(draft, encoding="utf-8")
        (out_dir / "findings.json").write_text(json.dumps({
            "study": {k: v for k, v in ct.meta.items()}, "qc": ct.qc,
            "spacing_mm": ct.spacing, "findings": [f.to_dict() for f in findings],
            "issues": [i.to_dict() for i in issues]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return StudyAnalysis(ct, findings, draft, issues, images, round(time.time() - t0, 1))
