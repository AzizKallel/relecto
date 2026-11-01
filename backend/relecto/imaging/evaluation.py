"""Score the AI nodule detection against reference outlines (e.g. LIDC-IDRI readers).

Matching: an AI finding matches a reference nodule when their centres are closer than
max(10 mm, half the reference long axis). Reported:
  - sensitivity, overall and on the clinically relevant subset (>= 6 mm, >= 2 readers agree)
  - false positives per scan (AI findings with no reference nodule; on LIDC some are real
    nodules < 3 mm or seen by readers as non-nodules, so this is an upper bound)
  - long-axis error (AI - reference) in mm, side agreement
"""

import json
from pathlib import Path
from statistics import median

import numpy as np


def match(ai_findings, ref_findings):
    pairs, used = [], set()
    for r in ref_findings:
        best, bd = None, None
        for k, a in enumerate(ai_findings):
            if k in used:
                continue
            d = float(np.linalg.norm(np.array(a.centroid_lps) - np.array(r.centroid_lps)))
            if d <= max(10.0, 0.5 * r.long_axis_mm) and (bd is None or d < bd):
                best, bd = k, d
        if best is not None:
            used.add(best)
        pairs.append((r, ai_findings[best] if best is not None else None, bd))
    fps = [a for k, a in enumerate(ai_findings) if k not in used]
    return pairs, fps


def evaluate_folders(folders, out=None) -> int:
    from .pipeline import analyze_study

    rows, per_study = [], []
    for folder in folders:
        a = analyze_study(folder, source="ai")
        pairs, fps = match(a.findings, a.reference)
        per_study.append({"study": Path(folder).name, "seconds": a.seconds, "timings": a.timings,
                          "ai": len(a.findings), "reference": len(a.reference), "false_positives": len(fps),
                          "fp_sizes_mm": [f.long_axis_mm for f in fps]})
        for r, m, d in pairs:
            rows.append({"study": Path(folder).name, "ref": r.finding_id, "readers": r.readers,
                         "ref_mm": r.long_axis_mm, "found": m is not None,
                         "ai_mm": m.long_axis_mm if m else None, "distance_mm": round(d, 1) if d is not None else None,
                         "side_ok": (m.side == r.side) if m else None, "lobe": m.lobe if m else None})
        print(f"{Path(folder).name}: {len(a.findings)} IA / {len(a.reference)} référence, "
              f"{sum(1 for r in rows if r['study'] == Path(folder).name and r['found'])} trouvés, "
              f"{len(fps)} en plus, {a.seconds} s")

    def sens(sub):
        return (sum(r["found"] for r in sub) / len(sub)) if sub else None

    relevant = [r for r in rows if r["ref_mm"] >= 6 and r["readers"] >= 2]
    errs = [r["ai_mm"] - r["ref_mm"] for r in rows if r["found"]]
    summary = {
        "studies": len(folders), "reference_nodules": len(rows),
        "sensitivity_all": sens(rows), "sensitivity_relevant": sens(relevant), "relevant_nodules": len(relevant),
        "false_positives_per_scan": sum(s["false_positives"] for s in per_study) / max(1, len(per_study)),
        "median_abs_size_error_mm": round(median(abs(e) for e in errs), 1) if errs else None,
        "median_size_bias_mm": round(median(errs), 1) if errs else None,
        "side_agreement": (sum(1 for r in rows if r["side_ok"]) / sum(1 for r in rows if r["found"])) if errs else None,
        "median_seconds_per_scan": median(s["seconds"] for s in per_study) if per_study else None,
    }
    print("\nRésumé")
    for k, v in summary.items():
        print(f"  {k:<28} {round(v, 3) if isinstance(v, float) else v}")
    print("\nDétail")
    for r in rows:
        print(f"  {r['study']:<16} {r['ref']:<4} {r['readers']} lect. {r['ref_mm']:>5.1f} mm  -> "
              + (f"trouvé {r['ai_mm']:.1f} mm à {r['distance_mm']} mm, {r['lobe'] or ''}" if r["found"] else "MANQUÉ"))
    if out:
        Path(out).write_text(json.dumps({"summary": summary, "nodules": rows, "studies": per_study},
                                        ensure_ascii=False, indent=2))
    return 0
