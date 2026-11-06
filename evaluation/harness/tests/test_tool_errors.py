"""Tool errors: Foundry aborts the response, the recorder must say which tool failed and keep the query."""

from __future__ import annotations

import json

import pytest

from foundry_eval import AttemptRecorder, RunStore
from foundry_eval.traces import build_trace_records, enrich_run, trace_query

# Verbatim shape of the exception text Foundry raised when execute_dax_query returned an error result.
FOUNDRY_TOOL_ERROR = (
    "An error occurred invoking 'execute_dax_query': "
    '{"code":"operation_rejected","message":"DAX query failed (0xc13a002c)"}\n'
    '{"code":"operation_rejected","message":"DAX query failed (0xc13a002c)"}\n'
    '{"io.modelcontextprotocol/serverInfo":{"name":"wealth-management-mcp"}}. Troubleshooting guide: https://x'
)


class Clock:
    t = 0.0

    def __call__(self) -> float:
        return self.t


def ev(kind: str, **fields):
    return {"type": kind, **fields}


def aborted_stream(clock: Clock) -> AttemptRecorder:
    rec = AttemptRecorder(run_id="r1", arm="dax", question_id="e07", question="Run bad DAX", clock=clock)

    def at(t: float, event: dict) -> None:
        clock.t = t
        rec.on_event(event)

    at(0.1, ev("response.created", response={"id": "resp_abort", "model": "gpt-6-luna", "agent": {"name": "a"}}))
    at(1.0, ev("response.output_item.added", output_index=0, item={"type": "mcp_list_tools", "id": "lt"}))
    at(1.5, ev("response.output_item.done", output_index=0, item={"type": "mcp_list_tools", "id": "lt"}))
    call = {"type": "mcp_call", "id": "mc1", "name": "execute_dax_query", "arguments": "", "output": None}
    at(3.0, ev("response.output_item.added", output_index=1, item=call))
    at(3.0, ev("response.mcp_call.in_progress", output_index=1, item_id="mc1"))
    at(3.1, ev("response.mcp_call_arguments.delta", output_index=1, item_id="mc1", delta='{"dax_query":"EVAL'))
    at(3.2, ev("response.mcp_call_arguments.delta", output_index=1, item_id="mc1", delta='UATE ROW(1)"}'))
    return rec


def test_aborted_stream_records_the_tool_error_with_its_query():
    clock = Clock()
    rec = aborted_stream(clock)
    clock.t = 5.2
    rec.fail(RuntimeError(FOUNDRY_TOOL_ERROR))
    attempt, steps = rec.build()

    assert attempt.status == "failed" and attempt.infra_error is False
    assert attempt.tool_error is True
    assert attempt.tool_error_tool == "execute_dax_query"
    assert attempt.tool_error_message == '{"code":"operation_rejected","message":"DAX query failed (0xc13a002c)"}'
    assert attempt.answer_text is None
    assert attempt.response_id == "resp_abort" and attempt.model == "gpt-6-luna"
    assert (attempt.tool_calls, attempt.tool_calls_failed, attempt.query_calls) == (1, 1, 1)
    assert attempt.duration_s == 5.2

    call = steps[1]
    assert call.tool_status == "failed" and "0xc13a002c" in (call.tool_error or "")
    assert call.query_text == "EVALUATE ROW(1)"
    assert call.timing_basis == "in_progress" and call.duration_s == pytest.approx(2.2)
    assert attempt.tool_time_s == pytest.approx(2.2)


def test_duration_is_fixed_when_the_error_arrives_not_when_the_attempt_is_built():
    clock = Clock()
    rec = aborted_stream(clock)
    clock.t = 5.0
    rec.fail(RuntimeError(FOUNDRY_TOOL_ERROR))
    clock.t = 500.0
    assert rec.build()[0].duration_s == 5.0


def test_other_exceptions_are_not_tool_errors():
    rec = AttemptRecorder(run_id="r1", arm="dax", question_id="q", question="?")
    rec.fail(TimeoutError("100 s cut-off"))
    attempt, _ = rec.build()
    assert attempt.tool_error is False and attempt.tool_error_tool is None and attempt.infra_error is True


def test_item_level_tool_failure_in_a_completed_response_is_also_a_tool_error():
    rec = AttemptRecorder(run_id="r1", arm="sql", question_id="q", question="?")
    bad = {
        "type": "mcp_call",
        "name": "execute_sql",
        "arguments": "{}",
        "output": None,
        "error": {"message": "boom\nsecond line"},
        "status": "failed",
    }
    rec.record_response({"status": "completed", "output": [bad]}, elapsed_s=2.0)
    attempt, _ = rec.build()
    assert attempt.status == "completed" and attempt.tool_error is True
    assert (attempt.tool_error_tool, attempt.tool_error_message) == ("execute_sql", "boom")


def test_streamed_tool_error_via_error_event():
    rec = AttemptRecorder(run_id="r1", arm="dax", question_id="q", question="?")
    rec.on_event(ev("response.created", response={"id": "resp_e"}))
    rec.on_event(ev("error", code="tool_error", message=FOUNDRY_TOOL_ERROR))
    attempt, _ = rec.build()
    assert attempt.tool_error is True and attempt.tool_error_tool == "execute_dax_query"
    assert attempt.infra_error is False


def agent_rows(rid: str, *, error: str = "", success: str = "False", code: str = "500", tool_ok: str = "True"):
    return [
        {"rid": rid, "operation_Id": "op1", "name": "invoke_agent a:1", "success": success,
         "resultCode": code, "duration": 3358.8, "tool": "", "err": error},
        {"rid": rid, "operation_Id": "op1", "name": "execute_tool mcp_semantic.execute_dax_query",
         "success": tool_ok, "resultCode": "0", "duration": 2202.4, "tool": "mcp_semantic.execute_dax_query", "err": ""},
    ]


def stored_run(tmp_path, *, failed: bool):
    store = RunStore.create(tmp_path, arm="dax")
    rec = store.recorder(question_id="q", question="?")
    if failed:
        rec.on_event(ev("response.created", response={"id": "resp_t"}))
        rec.fail(RuntimeError("An error occurred invoking 'execute_dax_query': {}"))
    else:
        rec.record_response({"id": "resp_t", "status": "completed", "output": []}, elapsed_s=1.0)
    store.add(rec)
    return store


def test_trace_record_reads_the_agent_span_because_the_tool_span_says_success(tmp_path):
    store = stored_run(tmp_path, failed=True)
    [trace] = build_trace_records(store.attempts(), agent_rows("resp_t", error="tool_user_error"))

    assert trace.tool_error is True and trace.error_type == "tool_user_error"
    assert trace.agent_span_success is False and trace.agent_result_code == "500"
    assert trace.agent_span_s == pytest.approx(3.3588)
    assert trace.tool_spans == 1 and trace.tool_names == ["mcp_semantic.execute_dax_query"]
    assert trace.trace_id == "op1"


def test_trace_adds_a_tool_error_the_stream_missed_and_rolls_up(tmp_path):
    store = stored_run(tmp_path, failed=False)
    assert store.finish().n_tool_errors == 0

    records, missing = enrich_run(
        store, "app", query=lambda app, kql: agent_rows("resp_t", error="tool_user_error")
    )
    assert (len(records), missing) == (1, 0)
    assert RunStore.open(tmp_path, store.run.run_id).run.n_tool_errors == 1
    assert store.run.status == "completed", "a trace join must not rewrite the run's status"


def test_stream_detected_tool_error_counts_once_even_when_traced_twice(tmp_path):
    store = stored_run(tmp_path, failed=True)
    rows = agent_rows("resp_t", error="tool_user_error")
    [trace] = build_trace_records(store.attempts(), rows)
    store.add_traces([trace, trace])
    assert store.finish().n_tool_errors == 1


def test_tool_time_comes_from_the_trace_and_parallel_spans_count_once(tmp_path):
    store = stored_run(tmp_path, failed=False)
    spans = [
        {"rid": "resp_t", "operation_Id": "op", "name": "invoke_agent a:1", "success": "True", "resultCode": "0",
         "duration": 12000, "timestamp": "2026-10-12T15:55:15.125Z", "tool": "", "err": ""},
        {"rid": "resp_t", "operation_Id": "op", "name": "execute_tool x", "success": "True", "resultCode": "0",
         "duration": 2000, "timestamp": "2026-10-12T15:55:16.000Z", "tool": "x", "err": ""},
        {"rid": "resp_t", "operation_Id": "op", "name": "execute_tool y", "success": "True", "resultCode": "0",
         "duration": 3000, "timestamp": "2026-10-12T15:55:17.000Z", "tool": "y", "err": ""},
        {"rid": "resp_t", "operation_Id": "op", "name": "execute_tool z", "success": "True", "resultCode": "0",
         "duration": 1000, "timestamp": "2026-10-12T15:55:25.000Z", "tool": "z", "err": ""},
    ]
    [trace] = build_trace_records(store.attempts(), spans)
    assert trace.tool_span_s == [2.0, 3.0, 1.0]
    assert trace.tool_time_s == pytest.approx(5.0), "x (1-3s) and y (2-5s) overlap, so 1s to 5s plus 1s for z"


def test_model_call_spans_give_tokens_cache_hits_and_a_timeline(tmp_path):
    store = stored_run(tmp_path, failed=False)
    base = {"rid": "resp_t", "operation_Id": "op", "success": "True", "resultCode": "0", "err": ""}
    spans = [
        {**base, "name": "invoke_agent a:1", "duration": 10000, "timestamp": "2026-10-20T10:00:00.000Z", "tool": ""},
        {**base, "name": "execute_tool q", "duration": 4000, "timestamp": "2026-10-20T10:00:01.000Z", "tool": "q"},
        {**base, "name": "chat m", "duration": 2000, "timestamp": "2026-10-20T10:00:07.000Z", "tool": "",
         "in_tok": "21760", "out_tok": "146", "cache_read": "20101", "cache_create": "1656", "finish": '["stop"]'},
    ]
    [trace] = build_trace_records(store.attempts(), spans)
    assert (trace.model_spans, trace.model_time_s) == (1, pytest.approx(2.0))
    assert (trace.model_input_tokens, trace.model_cache_read_tokens, trace.model_cache_creation_tokens) == (21760, 20101, 1656)
    assert trace.untraced_s == pytest.approx(4.0), "10 s agent span minus 4 s tool and 2 s model"
    assert [(s["kind"], s["start_s"]) for s in trace.spans] == [("gap", 0.0), ("tool", 1.0), ("gap", 5.0), ("model", 7.0), ("gap", 9.0)]


def test_proxy_timings_join_by_question_hash_and_nearest_start(tmp_path):
    import hashlib

    from foundry_eval.tool_timings import join_proxy_timings, read_proxy_log

    store = RunStore.create(tmp_path, arm="fabric")
    rec = store.recorder(question_id="q", question="How many clients?")
    rec.record_response(
        {"id": "resp_f", "status": "completed", "output": [
            {"type": "mcp_call", "id": "c1", "name": "ask_fabric_data_agent", "server_label": "s",
             "arguments": json.dumps({"question": "How many clients do we have?"}), "output": "3,000"},
        ]},
        elapsed_s=30.0,
    )
    attempt = store.add(rec)
    sha = hashlib.sha256(b"How many clients do we have?").hexdigest()
    started = attempt.started_at.isoformat()
    log = tmp_path / "proxy.log"
    log.write_text(
        "INFO fabric_dataagent_mcp: answered in 20.0s\n"
        f'INFO fabric_dataagent_mcp: timing {{"event": "timing", "started_at": "{started}", "question_sha256": "{sha}", '
        '"sign_in_s": 0.0, "session_open_s": 1.1, "upstream_call_s": 18.4, "session_close_s": 0.2, "total_s": 19.7, '
        '"is_error": false, "answer_chars": 40}\n',
        encoding="utf-8",
    )
    assert join_proxy_timings(store, read_proxy_log(log), source="test") == 1
    [timing] = store.tool_timings()
    assert (timing.upstream_call_s, timing.session_open_s, timing.total_s) == (18.4, 1.1, 19.7)
    assert join_proxy_timings(store, read_proxy_log(log), source="test") == 0, "a second pass adds nothing"


def test_attempts_without_spans_yet_are_left_for_a_later_pass(tmp_path):
    store = stored_run(tmp_path, failed=True)
    records, missing = enrich_run(store, "app", query=lambda app, kql: [])
    assert (records, missing) == ([], 1)
    assert store.traces() == []


def test_trace_query_rejects_ids_that_could_break_out_of_the_kql_string():
    kql = trace_query(["resp_ok", "x') or 1==1 //"])
    assert "resp_ok" in kql and "1==1" not in kql
    assert json.dumps(kql)
