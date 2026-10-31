"""Quality gate: measure the checker on the golden set and block regressions.

Rules for a release to pass:
  1. every enabled rule that fires on the golden set has precision >= MIN_PRECISION;
  2. no rule's precision or recall drops below the stored baseline (minus a small tolerance).

Golden sets live in clinical-content/golden-sets/*.jsonl (synthetic or
de-identified reports annotated by radiologists). The baseline is
clinical-content/golden-sets/baseline.json.
"""

import json
from collections import Counter
from pathlib import Path

from .checker import run_check
from .checker.content import content_dir

MIN_PRECISION = 0.50
TOLERANCE = 0.02


def golden_rows():
    rows = []
    for path in sorted((content_dir() / "golden-sets").glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            rows += [json.loads(line) for line in f if line.strip()]
    return rows


def evaluate(rows=None):
    rows = rows if rows is not None else golden_rows()
    tp, fp, fn, details = Counter(), Counter(), Counter(), []
    for row in rows:
        got = Counter(i.rule_id for i in run_check(row["text"], row["exam"], max_issues=100).issues)
        exp = Counter(row["expected"])
        for rule in set(got) | set(exp):
            t = min(got[rule], exp[rule])
            tp[rule] += t
            fp[rule] += got[rule] - t
            fn[rule] += exp[rule] - t
            if got[rule] > t:
                details.append((row["id"], "FALSE ALARM", rule))
            if exp[rule] > t:
                details.append((row["id"], "MISSED", rule))
    scores = {}
    for r in sorted(set(tp) | set(fp) | set(fn)):
        p = tp[r] / (tp[r] + fp[r]) if tp[r] + fp[r] else None
        rc = tp[r] / (tp[r] + fn[r]) if tp[r] + fn[r] else None
        scores[r] = {"tp": tp[r], "fp": fp[r], "fn": fn[r], "precision": p, "recall": rc}
    return len(rows), scores, details


def _fmt(x):
    return "  -  " if x is None else f"{x:.2f}"


def run_gate(update_baseline: bool = False) -> int:
    n, scores, details = evaluate()
    baseline_path = content_dir() / "golden-sets" / "baseline.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}

    print(f"\n{n} reports in the golden set\n")
    print(f"{'rule':<10}{'TP':>5}{'FP':>5}{'FN':>5}{'precision':>11}{'recall':>8}  status")
    failures = []
    for rule, s in scores.items():
        status = "ok"
        if s["precision"] is not None and s["precision"] < MIN_PRECISION:
            status = f"FAIL precision < {MIN_PRECISION}"
        b = baseline.get(rule)
        if b:
            for metric in ("precision", "recall"):
                if b.get(metric) is not None and s[metric] is not None and s[metric] < b[metric] - TOLERANCE:
                    status = f"FAIL {metric} dropped from {b[metric]:.2f}"
        if status != "ok":
            failures.append(rule)
        print(f"{rule:<10}{s['tp']:>5}{s['fp']:>5}{s['fn']:>5}{_fmt(s['precision']):>11}{_fmt(s['recall']):>8}  {status}")
    if details:
        print("\nTo review:")
        for d in details:
            print(f"  {d[0]:<32} {d[1]:<12} {d[2]}")

    if update_baseline:
        baseline_path.write_text(json.dumps({r: {"precision": s["precision"], "recall": s["recall"]}
                                             for r, s in scores.items()}, indent=2) + "\n")
        print(f"\nBaseline updated: {baseline_path.name}")
        return 0
    if failures:
        print(f"\nQUALITY GATE FAILED: {', '.join(failures)}")
        return 1
    print("\nQuality gate passed.")
    return 0
