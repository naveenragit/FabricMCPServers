"""Foundry cloud evaluation of stored responses with the built-in evaluators (plan 6.2, 6.3, 6.4).

Needs the `traces` extra's SDKs plus azure-ai-projects and openai. Inline response ids mean no storage is used, which keeps
this independent of the tenant's storage governance. Results are written back as EvaluationRecords.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from .records import AttemptRecord, EvaluationRecord
from .store import RunStore

# name -> (category, applies to arm 3 only in a limited way)
BUILTINS: dict[str, str] = {
    "relevance": "quality",
    "fluency": "quality",
    "coherence": "quality",
    "groundedness": "quality",
    "task_completion": "agentic",
    "intent_resolution": "agentic",
    "task_adherence": "agentic",
    "tool_call_accuracy": "agentic",
    "tool_call_success": "agentic",
    "tool_output_utilization": "agentic",
    "tool_selection": "agentic",
    "tool_input_accuracy": "agentic",
    "ungrounded_attributes": "risk",
}
_TRANSIENT = ("getaddrinfo", "Connection", "Timeout", "RateLimit", "InternalServer")


def _retry(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    for attempt in range(8):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == 7 or not any(t in f"{type(exc).__name__} {exc}" for t in _TRANSIENT):
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def run_builtin_evaluation(
    client: Any,
    attempts: list[AttemptRecord],
    judge_model: str,
    evaluators: dict[str, str] | None = None,
    *,
    name: str = "wm-eval",
    poll_s: float = 10.0,
    timeout_s: float = 1800.0,
) -> tuple[str, str, str, list[dict[str, Any]]]:
    """Create one eval with every evaluator and run it over the attempts' response ids; return (eval, run, status, items)."""
    evaluators = evaluators or BUILTINS
    criteria = [
        {
            "type": "azure_ai_evaluator",
            "name": short,
            "evaluator_name": f"builtin.{short}",
            "initialization_parameters": {"deployment_name": judge_model},
        }
        for short in evaluators
    ]
    ev = _retry(
        client.evals.create,
        name=name,
        data_source_config={"type": "azure_ai_source", "scenario": "responses"},
        testing_criteria=criteria,
    )
    run = _retry(
        client.evals.runs.create,
        eval_id=ev.id,
        name=name,
        data_source={
            "type": "azure_ai_responses",
            "item_generation_params": {
                "type": "response_retrieval",
                "data_mapping": {"response_id": "{{item.resp_id}}"},
                "source": {
                    "type": "file_content",
                    "content": [{"item": {"resp_id": a.response_id}} for a in attempts if a.response_id],
                },
            },
        },
    )
    deadline = time.monotonic() + timeout_s
    while True:
        run = _retry(client.evals.runs.retrieve, run.id, eval_id=ev.id)
        if run.status in ("completed", "failed", "canceled") or time.monotonic() > deadline:
            break
        time.sleep(poll_s)
    items: list[dict[str, Any]] = []
    if run.status == "completed":
        expected = len(attempts)
        # The run can report completed a moment before its items are listable.
        for _ in range(8):
            items = [
                i.model_dump(mode="json")
                for i in _retry(lambda: list(client.evals.runs.output_items.list(run.id, eval_id=ev.id)))
            ]
            if len(items) >= expected:
                break
            time.sleep(poll_s)
    return ev.id, run.id, run.status, items


def records_from_items(
    items: list[dict[str, Any]],
    attempts: list[AttemptRecord],
    judge_model: str,
    eval_id: str,
    run_id: str,
    evaluators: dict[str, str] | None = None,
) -> list[EvaluationRecord]:
    evaluators = evaluators or BUILTINS
    by_response = {a.response_id: a for a in attempts if a.response_id}
    records: list[EvaluationRecord] = []
    for item in items:
        response_id = (item.get("datasource_item") or {}).get("response_id")
        attempt = by_response.get(response_id)
        if attempt is None:
            continue
        for result in item.get("results", []):
            short = result.get("name", "")
            status = result.get("status") or "completed"
            ok = status == "completed" and result.get("score") is not None
            records.append(
                EvaluationRecord(
                    eval_id=uuid.uuid4().hex,
                    attempt_id=attempt.attempt_id,
                    run_id=attempt.run_id,
                    arm=attempt.arm,
                    question_id=attempt.question_id,
                    repeat_index=attempt.repeat_index,
                    evaluator=f"builtin.{short}",
                    evaluator_kind="builtin",
                    category=evaluators.get(short, "quality"),
                    status="ok" if ok else "error",
                    score=result.get("score") if ok else None,
                    passed=result.get("passed") if ok else None,
                    label=result.get("label"),
                    threshold=result.get("threshold"),
                    reason=(result.get("reason") or "")[:2000] or None,
                    judge_model=judge_model,
                    judge_input_tokens=(result.get("properties") or {}).get("prompt_tokens"),
                    judge_output_tokens=(result.get("properties") or {}).get("completion_tokens"),
                    evaluated_at=datetime.now(UTC),
                    foundry_eval_id=eval_id,
                    foundry_eval_run_id=run_id,
                    foundry_output_item_id=str(item.get("id")),
                )
            )
    return records


def evaluate_run_in_cloud(
    client: Any,
    store: RunStore,
    judge_model: str,
    evaluators: dict[str, str] | None = None,
    *,
    workers: int = 6,
) -> tuple[list[EvaluationRecord], dict[str, str]]:
    """One cloud eval per evaluator, in parallel, so a failure in one cannot hide the others.

    Only finished attempts without a tool error are sent (the others have no complete response to judge), and only the
    evaluator/attempt pairs that have no result yet, so a re-run fills gaps. Returns the records and failures by evaluator.
    """
    evaluators = evaluators or BUILTINS
    done = {(e.attempt_id, e.evaluator) for e in store.evaluations() if e.status == "ok"}
    base = [a for a in store.attempts() if a.response_id and a.status == "completed" and not a.tool_error]
    work = {
        short: [a for a in base if (a.attempt_id, f"builtin.{short}") not in done] for short in evaluators
    }
    work = {short: attempts for short, attempts in work.items() if attempts}
    if not work:
        return [], {}

    def one(short: str) -> list[EvaluationRecord]:
        attempts = work[short]
        eval_id, run_id, status, items = run_builtin_evaluation(
            client, attempts, judge_model, {short: evaluators[short]}, name=f"{store.run.run_id}:{short}"
        )
        if status != "completed":
            raise RuntimeError(f"cloud run {run_id} ended with status {status!r}")
        records = records_from_items(items, attempts, judge_model, eval_id, run_id, {short: evaluators[short]})
        got = len({r.attempt_id for r in records})
        if got < len(attempts):
            raise RuntimeError(f"cloud run {run_id} returned results for {got} of {len(attempts)} attempts")
        return records

    records: list[EvaluationRecord] = []
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {short: pool.submit(one, short) for short in work}
        for short, future in futures.items():
            try:
                found = future.result()
            except Exception as exc:
                failures[short] = f"{type(exc).__name__}: {exc}"[:300]
                continue
            store.add_evaluations(found)
            records += found
    return records, failures
