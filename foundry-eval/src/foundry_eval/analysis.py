"""Load stored runs into dataframes. Needs the `analysis` extra (pandas)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .records import TABLES
from .store import ATTEMPTS_FILE, EVALUATIONS_FILE, RUN_FILE, STEPS_FILE


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_tables(root: Path | str, run_ids: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Concatenate every run under root (or just run_ids) into one dataframe per table."""
    directories = sorted(p for p in Path(root).iterdir() if (p / RUN_FILE).exists())
    if run_ids is not None:
        directories = [p for p in directories if p.name in run_ids]
    rows: dict[str, list[dict]] = {name: [] for name in TABLES}
    for directory in directories:
        rows["runs"].append(json.loads((directory / RUN_FILE).read_text(encoding="utf-8")))
        rows["attempts"] += _jsonl(directory / ATTEMPTS_FILE)
        rows["steps"] += _jsonl(directory / STEPS_FILE)
        rows["evaluations"] += _jsonl(directory / EVALUATIONS_FILE)
    return {
        name: pd.DataFrame(rows[name], columns=list(model.model_fields)) for name, model in TABLES.items()
    }


def wide_attempts(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per attempt with `score__<evaluator>` and `passed__<evaluator>` columns appended."""
    attempts, evaluations = tables["attempts"], tables["evaluations"]
    scored = evaluations[evaluations["status"] == "ok"]
    frames = [attempts.set_index("attempt_id")]
    for value in ("score", "passed"):
        pivot = scored.pivot_table(index="attempt_id", columns="evaluator", values=value, aggfunc="first")
        frames.append(pivot.add_prefix(f"{value}__"))
    return pd.concat(frames, axis=1).reset_index()
