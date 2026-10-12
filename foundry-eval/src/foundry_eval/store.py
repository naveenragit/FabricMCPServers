"""Append-only on-disk store: one directory per run, four JSONL/JSON files."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .recorder import AttemptRecorder
from .records import Arm, AttemptRecord, EvaluationRecord, RunRecord, StepRecord

RUN_FILE = "run.json"
ATTEMPTS_FILE = "attempts.jsonl"
STEPS_FILE = "steps.jsonl"
EVALUATIONS_FILE = "evaluations.jsonl"


def new_run_id(arm: str) -> str:
    return f"{arm}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


def _append(path: Path, records: Iterable[BaseModel]) -> None:
    lines = "".join(r.model_dump_json() + "\n" for r in records)
    if lines:
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(lines)


def _read(path: Path, model: type[BaseModel]) -> list[Any]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [model.model_validate_json(line) for line in fh if line.strip()]


class RunStore:
    def __init__(self, directory: Path, run: RunRecord, started: float | None) -> None:
        self.directory = directory
        self.run = run
        self._started = started

    @classmethod
    def create(cls, root: Path | str, *, arm: Arm, run_id: str | None = None, **fields: Any) -> RunStore:
        run = RunRecord(run_id=run_id or new_run_id(arm), arm=arm, started_at=datetime.now(UTC), **fields)
        directory = Path(root) / run.run_id
        directory.mkdir(parents=True, exist_ok=False)
        store = cls(directory, run, time.perf_counter())
        store._write_run()
        return store

    @classmethod
    def open(cls, root: Path | str, run_id: str) -> RunStore:
        directory = Path(root) / run_id
        run = RunRecord.model_validate_json((directory / RUN_FILE).read_text(encoding="utf-8"))
        return cls(directory, run, None)

    def _write_run(self) -> None:
        tmp = self.directory / f"{RUN_FILE}.tmp"
        tmp.write_text(self.run.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self.directory / RUN_FILE)

    def recorder(
        self, *, question_id: str, question: str, repeat_index: int = 0, **kwargs: Any
    ) -> AttemptRecorder:
        return AttemptRecorder(
            run_id=self.run.run_id,
            arm=self.run.arm,
            question_id=question_id,
            question=question,
            repeat_index=repeat_index,
            schema_tools=self.run.schema_tools,
            **kwargs,
        )

    def add(self, recorder: AttemptRecorder) -> AttemptRecord:
        attempt, steps = recorder.build()
        self.add_attempt(attempt, steps)
        return attempt

    def add_attempt(self, attempt: AttemptRecord, steps: Iterable[StepRecord]) -> None:
        _append(self.directory / STEPS_FILE, steps)
        _append(self.directory / ATTEMPTS_FILE, [attempt])

    def add_evaluations(self, evaluations: Iterable[EvaluationRecord]) -> None:
        """Works on a reopened run, so cloud evaluations can land after the run has finished."""
        _append(self.directory / EVALUATIONS_FILE, evaluations)

    def attempts(self) -> list[AttemptRecord]:
        return _read(self.directory / ATTEMPTS_FILE, AttemptRecord)

    def finish(self, status: str | None = None) -> RunRecord:
        attempts = self.attempts()
        run = self.run
        run.ended_at = datetime.now(UTC)
        if self._started is not None:
            run.wall_time_s = time.perf_counter() - self._started
        run.n_attempts = len(attempts)
        run.n_completed = sum(a.status == "completed" for a in attempts)
        run.n_failed = sum(a.status in ("failed", "timeout") for a in attempts)
        run.n_infra_errors = sum(a.infra_error for a in attempts)
        run.input_tokens = sum(a.input_tokens or 0 for a in attempts)
        run.cached_input_tokens = sum(a.cached_input_tokens or 0 for a in attempts)
        run.output_tokens = sum(a.output_tokens or 0 for a in attempts)
        run.reasoning_tokens = sum(a.reasoning_tokens or 0 for a in attempts)
        run.total_tokens = sum(a.total_tokens or 0 for a in attempts)
        run.tool_time_s = sum(a.tool_time_s or 0.0 for a in attempts)
        run.reasoning_time_s = sum(a.reasoning_time_s or 0.0 for a in attempts)
        run.attempts_duration_s = sum(a.duration_s for a in attempts)
        if status:
            run.status = status  # type: ignore[assignment]
        elif not attempts or run.n_failed == len(attempts):
            run.status = "failed"
        else:
            run.status = "partial" if run.n_failed else "completed"
        self._write_run()
        return run


def write_json_schemas(directory: Path | str) -> list[Path]:
    from .records import TABLES

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, model in TABLES.items():
        path = out / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths
