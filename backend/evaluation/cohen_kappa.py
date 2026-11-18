"""Cohen's kappa for the two-annotator relevance sheet.

Fill ``annotator_a`` / ``annotator_b`` in ``annotation_sheet.csv`` with grades
0-3, then run:

    .venv/Scripts/python.exe evaluation/cohen_kappa.py

Prints observed agreement, expected agreement, Cohen's kappa and the per-grade
confusion counts. Kappa needs two *independent* human annotators; labels
produced by the evaluation harness itself are not independent.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

SHEET = Path(__file__).resolve().parent / "annotation_sheet.csv"


def kappa(pairs: list[tuple[int, int]]) -> tuple[float, float, float]:
    n = len(pairs)
    if n == 0:
        raise SystemExit("No labelled pairs found.")
    observed = sum(1 for a, b in pairs if a == b) / n
    count_a = Counter(a for a, _ in pairs)
    count_b = Counter(b for _, b in pairs)
    expected = sum((count_a[g] / n) * (count_b[g] / n) for g in set(count_a) | set(count_b))
    return observed, expected, (observed - expected) / (1 - expected) if expected < 1 else 0.0


def main() -> None:
    if not SHEET.exists():
        raise SystemExit(f"Missing {SHEET.name}; run the evaluation harness first.")

    pairs: list[tuple[int, int]] = []
    unlabelled = 0
    with SHEET.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            a, b = row["annotator_a"].strip(), row["annotator_b"].strip()
            if not a and not b:
                unlabelled += 1
                continue
            if a not in {"0", "1", "2", "3"} or b not in {"0", "1", "2", "3"}:
                raise SystemExit(f"Row {row['pair_id']}: grades must be 0-3, got {a!r}/{b!r}.")
            pairs.append((int(a), int(b)))

    observed, expected, value = kappa(pairs)
    interpretation = (
        "poor" if value < 0 else "slight" if value < 0.21 else "fair" if value < 0.41
        else "moderate" if value < 0.61 else "substantial" if value < 0.81 else "almost perfect"
    )
    print(f"labelled pairs : {len(pairs)} ({unlabelled} unlabelled)")
    print(f"observed agreement : {observed:.3f}")
    print(f"expected agreement : {expected:.3f}")
    print(f"Cohen's kappa      : {value:.3f} ({interpretation})")
    if len(pairs) < 100:
        print("warning: fewer than 100 labelled pairs; kappa is unstable.", file=sys.stderr)


if __name__ == "__main__":
    main()
