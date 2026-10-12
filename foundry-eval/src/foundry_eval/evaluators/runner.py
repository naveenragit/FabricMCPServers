"""Run evaluators over a stored run and write the results as EvaluationRecords."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from ..dataset import Row
from ..records import AttemptRecord, EvaluationRecord, StepRecord
from ..store import RunStore
from .base import Case, EvalConfig, Evaluator, Outcome


def evaluate_attempt(
    attempt: AttemptRecord,
    steps: list[StepRecord],
    row: Row,
    evaluators: Iterable[Evaluator],
    config: EvalConfig,
    *,
    judge_model: str | None = None,
) -> list[EvaluationRecord]:
    case = Case(row=row, attempt=attempt, steps=steps, config=config)
    records: list[EvaluationRecord] = []
    for evaluator in evaluators:
        started = time.perf_counter()
        try:
            outcome = evaluator.run(case)
        except Exception as exc:  # an evaluator bug must not stop the run
            outcome = Outcome(None, f"{type(exc).__name__}: {exc}", status="error")
        passed = outcome.passed
        if outcome.status != "ok":
            passed = None
        records.append(
            EvaluationRecord(
                eval_id=uuid.uuid4().hex,
                attempt_id=attempt.attempt_id,
                run_id=attempt.run_id,
                arm=attempt.arm,
                question_id=attempt.question_id,
                repeat_index=attempt.repeat_index,
                evaluator=evaluator.name,
                evaluator_kind=evaluator.kind,
                category=evaluator.category,
                evaluator_version=evaluator.version,
                status=outcome.status,
                score=outcome.score,
                passed=passed,
                label=outcome.label,
                reason=outcome.reason[:2000],
                details=outcome.details,
                judge_model=judge_model if evaluator.kind == "custom_prompt" else None,
                judge_input_tokens=outcome.judge_input_tokens,
                judge_output_tokens=outcome.judge_output_tokens,
                duration_s=time.perf_counter() - started,
                evaluated_at=datetime.now(UTC),
            )
        )
    return records


def evaluate_run(
    store: RunStore,
    rows_by_id: dict[str, Row],
    evaluators: list[Evaluator],
    config: EvalConfig,
    *,
    judge_model: str | None = None,
    skip_done: bool = True,
) -> list[EvaluationRecord]:
    """Evaluate every attempt in the run that has a dataset row; re-running skips finished evaluator/attempt pairs."""
    steps_by_attempt: dict[str, list[StepRecord]] = {}
    for step in store.steps():
        steps_by_attempt.setdefault(step.attempt_id, []).append(step)
    done = {(e.attempt_id, e.evaluator) for e in store.evaluations() if e.status == "ok"} if skip_done else set()
    written: list[EvaluationRecord] = []
    for attempt in store.attempts():
        row = rows_by_id.get(attempt.question_id)
        if row is None:
            continue
        pending = [e for e in evaluators if (attempt.attempt_id, e.name) not in done]
        if not pending:
            continue
        records = evaluate_attempt(
            attempt, sorted(steps_by_attempt.get(attempt.attempt_id, []), key=lambda s: s.index), row, pending, config, judge_model=judge_model
        )
        store.add_evaluations(records)
        written += records
    return written
