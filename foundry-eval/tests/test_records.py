"""Record, store and reload attempts using hand-built events; no network and no SDK needed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from foundry_eval import AttemptRecorder, EvaluationRecord, RunStore, write_json_schemas
from foundry_eval.analysis import load_tables, wide_attempts


class Clock:
    t = 0.0

    def __call__(self) -> float:
        return self.t


def ev(kind: str, **fields):
    return {"type": kind, **fields}


USAGE = {
    "input_tokens": 12000,
    "input_tokens_details": {"cached_tokens": 4000, "cache_write_tokens": 500},
    "output_tokens": 900,
    "output_tokens_details": {"reasoning_tokens": 600},
    "total_tokens": 12900,
}

TOOLS = {"type": "mcp_list_tools", "id": "lt1", "server_label": "dax"}
THINK = {"type": "reasoning", "id": "rs1", "summary": [{"type": "summary_text", "text": "Plan a query"}]}
CALL_OK = {
    "type": "mcp_call",
    "id": "mc1",
    "server_label": "dax",
    "name": "execute_dax_query",
    "arguments": json.dumps({"model_id": "m", "query": 'EVALUATE ROW("n", 1)'}),
    "output": "x" * 5000,
    "status": "completed",
}
CALL_BAD = {
    "type": "mcp_call",
    "id": "mc2",
    "server_label": "dax",
    "name": "get_semantic_model_schema",
    "arguments": "{}",
    "output": None,
    "error": {"message": "boom"},
    "status": "failed",
}
MESSAGE = {
    "type": "message",
    "id": "msg1",
    "content": [{"type": "output_text", "text": "There are 3,000 clients."}],
}


def streamed_attempt():
    clock = Clock()
    rec = AttemptRecorder(
        run_id="r1",
        arm="dax",
        question_id="q01",
        question="How many clients?",
        schema_tools=["get_semantic_model_schema"],
        clock=clock,
    )

    def at(t: float, event: dict) -> None:
        clock.t = t
        rec.on_event(event)

    at(0.5, ev("response.created"))
    at(0.5, ev("response.output_item.added", output_index=0, item={**TOOLS}))
    at(0.6, ev("response.mcp_list_tools.in_progress", output_index=0, item_id="lt1"))
    at(1.1, ev("response.mcp_list_tools.completed", output_index=0, item_id="lt1"))
    at(1.1, ev("response.output_item.done", output_index=0, item=TOOLS))
    at(1.1, ev("response.output_item.added", output_index=1, item={"type": "reasoning", "id": "rs1"}))
    at(4.1, ev("response.output_item.done", output_index=1, item=THINK))
    at(4.1, ev("response.output_item.added", output_index=2, item={"type": "mcp_call", "id": "mc1"}))
    at(5.0, ev("response.mcp_call_arguments.done", output_index=2, item_id="mc1"))
    at(5.0, ev("response.mcp_call.in_progress", output_index=2, item_id="mc1"))
    at(9.0, ev("response.mcp_call.completed", output_index=2, item_id="mc1"))
    at(9.0, ev("response.output_item.done", output_index=2, item=CALL_OK))
    at(10.0, ev("response.output_item.added", output_index=3, item={"type": "mcp_call", "id": "mc2"}))
    at(10.5, ev("response.mcp_call_arguments.done", output_index=3, item_id="mc2"))
    at(11.0, ev("response.mcp_call.failed", output_index=3, item_id="mc2"))
    at(11.0, ev("response.output_item.done", output_index=3, item=CALL_BAD))
    at(12.0, ev("response.output_item.added", output_index=4, item={"type": "message", "id": "msg1"}))
    at(12.5, ev("response.output_text.delta", output_index=4, delta="There"))
    at(14.0, ev("response.output_item.done", output_index=4, item=MESSAGE))
    final = {
        "id": "resp_1",
        "status": "completed",
        "model": "gpt-5-2026",
        "usage": USAGE,
        "output": [TOOLS, THINK, CALL_OK, CALL_BAD, MESSAGE],
    }
    at(14.0, ev("response.completed", response=final))
    return rec.build()


def test_stream_splits_time_into_thinking_tool_and_rest():
    attempt, steps = streamed_attempt()

    assert attempt.timing_basis == "stream"
    assert attempt.duration_s == 14.0
    assert attempt.time_to_first_event_s == 0.5
    assert attempt.time_to_first_token_s == 12.5
    assert attempt.reasoning_time_s == pytest.approx(3.0)
    assert attempt.tool_list_time_s == pytest.approx(0.5)
    assert attempt.tool_time_s == pytest.approx(4.0 + 0.5)
    assert attempt.model_other_s == pytest.approx(14.0 - 3.0 - 0.5 - 4.5)

    assert [s.kind for s in steps] == ["tool_list", "reasoning", "tool_call", "tool_call", "message"]
    first_call = steps[2]
    assert first_call.timing_basis == "args_done"
    assert first_call.start_s == 5.0
    assert first_call.duration_s == pytest.approx(4.0)
    assert first_call.query_text == 'EVALUATE ROW("n", 1)'
    assert first_call.output_chars == 5000
    assert len(first_call.output_preview or "") == 2000
    assert steps[1].summary_text == "Plan a query"


def test_tokens_model_answer_and_tool_counts():
    attempt, _ = streamed_attempt()

    assert (attempt.input_tokens, attempt.cached_input_tokens, attempt.cache_write_tokens) == (
        12000,
        4000,
        500,
    )
    assert (attempt.output_tokens, attempt.reasoning_tokens, attempt.total_tokens) == (900, 600, 12900)
    assert attempt.model == "gpt-5-2026"
    assert attempt.answer_text == "There are 3,000 clients."
    assert attempt.status == "completed"
    assert (attempt.tool_calls, attempt.tool_calls_failed, attempt.tool_list_calls) == (2, 1, 1)
    assert (attempt.schema_calls, attempt.query_calls) == (1, 1)


class FakeResponse(BaseModel):
    id: str
    status: str
    model: str
    usage: dict
    output: list[dict]


def test_parallel_tool_calls_are_not_double_counted():
    clock = Clock()
    rec = AttemptRecorder(run_id="r1", arm="dax", question_id="q", question="?", clock=clock)
    final = {"status": "completed", "output": [CALL_OK, {**CALL_OK, "id": "mc9"}]}
    for index, call_id in enumerate(("mc1", "mc9")):
        clock.t = 1.0
        rec.on_event(
            ev("response.output_item.added", output_index=index, item={"type": "mcp_call", "id": call_id})
        )
        clock.t = 2.0
        rec.on_event(ev("response.mcp_call_arguments.done", output_index=index, item_id=call_id))
        clock.t = 10.0
        rec.on_event(ev("response.mcp_call.completed", output_index=index, item_id=call_id))
    clock.t = 11.0
    rec.on_event(ev("response.completed", response=final))
    attempt, steps = rec.build()

    assert [s.duration_s for s in steps] == [8.0, 8.0]
    assert attempt.tool_time_s == pytest.approx(8.0)
    assert attempt.model_other_s == pytest.approx(3.0)


def test_malformed_and_unrelated_events_are_ignored():
    rec = AttemptRecorder(run_id="r1", arm="dax", question_id="q", question="?")
    rec.on_event(ev("response.output_item.added", item={"type": "message"}))
    rec.on_event(ev("response.reasoning_summary_text.delta", delta="x"))
    rec.on_event(ev("ping"))
    attempt, steps = rec.build()
    assert steps == [] and attempt.status == "failed" and attempt.error_type == "no_final_response"
    assert attempt.infra_error, "a stream that never finished is retried as a transport failure"
    with pytest.raises(TypeError):
        rec.record_response(object())


def test_retry_is_linked_and_message_text_alone_does_not_mark_infra():
    rec = AttemptRecorder(run_id="r1", arm="sql", question_id="q", question="?", retry_of="abc")
    rec.fail(ValueError("connection string is malformed"))
    attempt, _ = rec.build()
    assert attempt.retry_of == "abc" and attempt.infra_error is False


def test_rate_limit_and_failed_dependency_are_infra_but_a_tool_error_is_not():
    from openai import APIError

    def attempt_for(message):
        rec = AttemptRecorder(run_id="r1", arm="dax", question_id="q", question="?")
        rec.fail(APIError(message, request=None, body=None))
        return rec.build()[0]

    assert attempt_for("Model deployment rate limit exceeded. Your requests have exceeded token rate limit.").infra_error
    assert attempt_for("[Failed Dependency] while invoking tool x, the dependency call to the MCP server failed").infra_error
    tool = attempt_for("An error occurred invoking 'x': Model rate limit exceeded upstream")
    assert tool.tool_error and not tool.infra_error
    assert not attempt_for("The model deployment encountered an error processing your request. You can retry.").infra_error
    overload = attempt_for("An error occurred invoking 'ask_fabric_data_agent': The Data Agent run failed before producing a result.")
    assert overload.tool_error and overload.infra_error, "the Data Agent's load rejection is retried, and still recorded"


def test_a_408_from_foundry_is_a_retried_timeout():
    class Status408(Exception):
        status_code = 408

    rec = AttemptRecorder(run_id="r1", arm="fabric", question_id="q", question="?")
    rec.fail(Status408("Error code: 408 - The operation was timeout."))
    attempt, _ = rec.build()
    assert (attempt.status, attempt.infra_error) == ("timeout", True)


def test_non_streaming_records_totals_only_and_accepts_sdk_style_models():
    rec = AttemptRecorder(run_id="r1", arm="sql", question_id="q02", question="?")
    rec.record_response(
        FakeResponse(id="resp_2", status="completed", model="m", usage=USAGE, output=[CALL_OK, MESSAGE]),
        elapsed_s=7.5,
    )
    attempt, steps = rec.build()

    assert attempt.timing_basis == "total_only"
    assert attempt.duration_s == 7.5
    assert attempt.tool_time_s is None and attempt.model_other_s is None
    assert attempt.reasoning_tokens == 600
    assert all(s.timing_basis == "none" and s.duration_s is None for s in steps)


def test_missing_usage_stays_none_not_zero():
    rec = AttemptRecorder(run_id="r1", arm="sql", question_id="q", question="?")
    rec.record_response({"status": "completed", "output": [MESSAGE]}, elapsed_s=1.0)
    attempt, _ = rec.build()
    assert attempt.input_tokens is None and attempt.reasoning_tokens is None


def test_failed_response_exception_and_infra_classification():
    rec = AttemptRecorder(run_id="r1", arm="fabric", question_id="q", question="?")
    rec.on_event(
        ev(
            "response.failed",
            response={"status": "failed", "error": {"code": "server_error", "message": "x"}},
        )
    )
    attempt, _ = rec.build()
    assert (attempt.status, attempt.error_type, attempt.infra_error) == ("failed", "server_error", True)

    rec = AttemptRecorder(run_id="r1", arm="fabric", question_id="q", question="?")
    rec.fail(TimeoutError("100 s cut-off"))
    attempt, _ = rec.build()
    assert (attempt.status, attempt.infra_error) == ("timeout", True)

    rec = AttemptRecorder(run_id="r1", arm="fabric", question_id="q", question="?")
    rec.fail(ValueError("bad payload"))
    attempt, _ = rec.build()
    assert (attempt.status, attempt.infra_error) == ("failed", False)

    rec = AttemptRecorder(run_id="r1", arm="fabric", question_id="q", question="?")
    rec.record_response(
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, elapsed_s=2
    )
    assert rec.build()[0].incomplete_reason == "max_output_tokens"


def test_store_roundtrip_evaluations_and_wide_table(tmp_path):
    store = RunStore.create(tmp_path, arm="dax", agent_model="gpt-5")

    ok = store.recorder(question_id="q01", question="How many clients?")
    ok.record_response(
        {"id": "a", "status": "completed", "usage": USAGE, "output": [CALL_OK, MESSAGE]}, elapsed_s=6.0
    )
    store.add(ok)
    bad = store.recorder(question_id="q02", question="?", repeat_index=1)
    bad.fail(TimeoutError("t"))
    store.add(bad)
    run = store.finish()

    assert (run.status, run.n_attempts, run.n_completed, run.n_failed, run.n_infra_errors) == (
        "partial",
        2,
        1,
        1,
        1,
    )
    assert run.wall_time_s is not None and run.wall_time_s >= 0
    assert RunStore.create(tmp_path, arm="sql").run.schema_tools == ["get_database_schema"]

    reopened = RunStore.open(tmp_path, run.run_id)
    attempt = reopened.attempts()[0]
    reopened.add_evaluations(
        [
            EvaluationRecord(
                eval_id="e1",
                attempt_id=attempt.attempt_id,
                run_id=run.run_id,
                arm="dax",
                question_id="q01",
                evaluator="numeric_audit",
                evaluator_kind="custom_code",
                category="accuracy",
                score=1.0,
                passed=True,
                evaluated_at=datetime.now(UTC),
                details={"criteria": {"c1": True}},
            )
        ]
    )

    tables = load_tables(tmp_path, run_ids=[run.run_id])
    assert set(tables) == {"runs", "attempts", "steps", "evaluations", "traces"}
    assert str(tables["attempts"]["started_at"].dtype).startswith("datetime64")
    assert len(tables["attempts"]) == 2 and len(tables["steps"]) == 2 and len(tables["evaluations"]) == 1
    wide = wide_attempts(tables)
    row = wide[wide["question_id"] == "q01"].iloc[0]
    assert row["score__numeric_audit"] == 1.0 and row["input_tokens"] == 12000
    assert wide[wide["question_id"] == "q02"]["score__numeric_audit"].isna().all()


def test_schema_files_cover_every_table_and_match_the_committed_copies(tmp_path):
    paths = write_json_schemas(tmp_path)
    assert {p.name for p in paths} == {
        f"{n}.schema.json" for n in ("runs", "attempts", "steps", "evaluations", "traces")
    }
    committed = Path(__file__).parents[1] / "schemas"
    for path in paths:
        assert (committed / path.name).read_text(encoding="utf-8") == path.read_text(encoding="utf-8"), (
            "schema drift: run `python -m foundry_eval schemas foundry-eval/schemas`"
        )
