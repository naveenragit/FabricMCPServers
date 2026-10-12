"""Build evals/eval-dataset.jsonl (84 rows) from the answer-key rows, the two markdown docs and the local overrides.

Run: .\.venv\Scripts\python.exe scripts\build_eval_dataset.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from foundry_eval.build_dataset import build_dataset, load_overrides
from foundry_eval.dataset import dataset_sha256, write_dataset

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "eval-dataset.jsonl"


def main() -> int:
    answer_rows = [
        json.loads(line)
        for line in (ROOT / "evals" / "wealth-management-eval.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = build_dataset(
        answer_rows,
        (ROOT / "docs" / "evaluation-questions.md").read_text(encoding="utf-8"),
        (ROOT / "docs" / "evaluation-risk-questions.md").read_text(encoding="utf-8"),
        load_overrides(ROOT / "artifacts" / "evaluation" / "dataset-overrides.json"),
    )
    write_dataset(OUT, rows)
    print(f"{len(rows)} rows -> {OUT.relative_to(ROOT)}  sha256={dataset_sha256(OUT)[:16]}")
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
