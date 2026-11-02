"""Validate the canonical evals/eval-dataset.jsonl without regenerating it from legacy sources.

Run: .\.venv\Scripts\python.exe scripts\build_eval_dataset.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from foundry_eval.dataset import dataset_sha256, load_dataset

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "eval-dataset.jsonl"


def main() -> int:
    rows = load_dataset(OUT)
    counts = Counter(row.id for row in rows)
    duplicates = sorted(row_id for row_id, count in counts.items() if count > 1)
    if not rows or duplicates:
        raise ValueError(f"Dataset must be nonempty with unique IDs; duplicates: {duplicates}")
    for row in rows:
        if not row.query.strip() or set(row.applies_to) != {"dax", "sql", "fabric"}:
            raise ValueError(f"{row.id}: expected a nonempty prompt shared by all three arms")
        criterion_ids = [criterion.id for criterion in row.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError(f"{row.id}: duplicate criterion IDs")
        if any(expected.criterion not in criterion_ids for expected in row.expected):
            raise ValueError(f"{row.id}: numeric expectation has no matching criterion")
        if not row.criteria and not row.expected_behavior:
            raise ValueError(f"{row.id}: missing scoring criteria or expected behavior")
    print(f"Validated {len(rows)} rows in {OUT.relative_to(ROOT)}  sha256={dataset_sha256(OUT)[:16]}")
    print("by set:", dict(Counter(r.set for r in rows)))
    print("by split:", dict(Counter(r.split for r in rows)))
    print("zero-tolerance rows:", sum(r.gate == "zero" for r in rows))
    print("answerable:", sum(r.answerable for r in rows))
    print("rows with no arms (not runnable yet):", [r.id for r in rows if not r.applies_to])
    bare = [
        f"{r.id}:{c.id}"
        for r in rows
        for c in r.criteria
        if c.kind == "fact" and not any(e.criterion == c.id for e in r.expected)
    ]
    print("fact criteria with no derived number (judge-only):", bare)
    return 0


if __name__ == "__main__":
    sys.exit(main())
