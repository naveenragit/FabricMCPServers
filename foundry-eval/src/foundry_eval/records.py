"""Standard record formats for one evaluation run per arm.

Four flat tables, joined by ids, so they load straight into a dataframe:

    runs         one row per arm execution        (run_id)
    attempts     one row per question x repeat    (attempt_id -> run_id)
    steps        one row per output item          (attempt_id)
    evaluations  one row per attempt x evaluator  (attempt_id)

Unknown values are None, never 0, so "not reported" stays distinguishable from "zero".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

Arm = Literal["dax", "sql", "fabric"]
RunStatus = Literal["running", "completed", "partial", "failed"]
AttemptStatus = Literal["completed", "incomplete", "failed", "timeout"]
StepKind = Literal["reasoning", "tool_list", "tool_call", "message", "other"]
TimingBasis = Literal["stream", "total_only"]
StepTimingBasis = Literal["args_done", "in_progress", "item_span", "none"]
EvaluatorKind = Literal["builtin", "custom_code", "custom_prompt", "red_team", "human"]
EvaluatorCategory = Literal["accuracy", "agentic", "quality", "risk", "safety"]
EvaluationStatus = Literal["ok", "error", "skipped"]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = SCHEMA_VERSION


class RunRecord(_Record):
    """One execution of one arm: what was run, and the total time the agent code took."""

    run_id: str
    batch_id: str | None = Field(None, description="Groups the arms launched together")
    arm: Arm
    status: RunStatus = "running"
    started_at: datetime
    ended_at: datetime | None = None
    wall_time_s: float | None = Field(None, description="Total time the arm's agent code took to run")

    agent_name: str | None = None
    agent_version: str | None = None
    agent_model: str | None = Field(None, description="Deployment the agent was configured with")
    reasoning_effort: str | None = None
    judge_model: str | None = None
    instructions_sha256: str | None = None
    policy_version: str | None = None
    tools: list[str] = Field(default_factory=list, description="server_label:tool_name, as allowlisted")
    schema_tools: list[str] = Field(
        default_factory=list, description="Tool names counted as schema discovery rather than querying"
    )

    dataset_name: str | None = None
    dataset_sha256: str | None = None
    dataset_split: str | None = None
    repeats: int | None = None
    concurrency: int | None = None

    git_sha: str | None = None
    git_dirty: bool | None = None
    harness_version: str | None = None
    environment: dict[str, str] = Field(
        default_factory=dict, description="Free-form: region, capacity SKU and state, cache warm or cold"
    )

    # Roll-ups, filled in by RunStore.finish(); token and time totals are left to analysis so a
    # missing usage value cannot silently undercount them.
    n_attempts: int = 0
    n_completed: int = 0
    n_failed: int = 0
    n_infra_errors: int = 0
    n_tool_errors: int = Field(
        0, description="Attempts in which a tool returned an error, seen in the stream or in the Foundry trace"
    )
    attempts_duration_s: float = Field(
        0.0, description="Sum of attempt durations; differs from wall time when attempts run concurrently"
    )


class AttemptRecord(_Record):
    """One agent call: a question asked once, on one arm."""

    attempt_id: str
    run_id: str
    arm: Arm
    question_id: str
    repeat_index: int = 0
    retry_of: str | None = Field(None, description="attempt_id of the platform-failed attempt this re-runs")
    question: str
    answer_text: str | None = None

    status: AttemptStatus
    error_type: str | None = None
    error_message: str | None = None
    incomplete_reason: str | None = None
    infra_error: bool = Field(
        False, description="The platform failed, not the agent; exclude from quality comparisons"
    )
    tool_error: bool = Field(
        False,
        description="A tool returned an error. Foundry aborts the whole response when that happens, "
        "so the attempt has no answer and the agent could not retry",
    )
    tool_error_tool: str | None = Field(None, description="Name of the tool that returned the error")
    tool_error_message: str | None = Field(None, description="The tool's error text, first line only")

    started_at: datetime
    ended_at: datetime
    duration_s: float = Field(description="Client wall clock, request sent to final event")
    timing_basis: TimingBasis
    time_to_first_event_s: float | None = None
    time_to_first_token_s: float | None = None
    reasoning_time_s: float | None = Field(None, description="Model thinking, from reasoning item spans")
    tool_time_s: float | None = Field(
        None,
        description="Stream estimate. Foundry emits mcp_call_arguments.done and mcp_call.completed together, so this is "
        "close to zero and is not tool execution time; use TraceRecord.tool_time_s",
    )
    tool_list_time_s: float | None = Field(None, description="MCP tool discovery")
    model_other_s: float | None = Field(
        None, description="Remainder: answer and argument generation, queueing, network"
    )

    response_id: str | None = None
    conversation_id: str | None = None
    model: str | None = Field(None, description="Model the platform reports it used")
    agent_name: str | None = None
    agent_version: str | None = None

    input_tokens: int | None = None
    cached_input_tokens: int | None = Field(None, description="Part of input_tokens")
    cache_write_tokens: int | None = None
    output_tokens: int | None = Field(None, description="Includes reasoning tokens")
    reasoning_tokens: int | None = Field(None, description="Part of output_tokens")
    total_tokens: int | None = None

    tool_calls: int = 0
    tool_calls_failed: int = 0
    tool_list_calls: int = 0
    schema_calls: int = 0
    query_calls: int = 0
    answer_chars: int | None = None


class StepRecord(_Record):
    """One output item of an attempt, in order."""

    step_id: str
    attempt_id: str
    run_id: str
    arm: Arm
    index: int
    kind: StepKind
    item_type: str | None = None

    start_s: float | None = Field(None, description="Offset from attempt start")
    duration_s: float | None = None
    timing_basis: StepTimingBasis = "none"

    server_label: str | None = None
    tool_name: str | None = None
    is_schema_call: bool | None = None
    arguments: str | None = None
    query_text: str | None = Field(None, description="The DAX, SQL or natural-language question sent")
    output_chars: int | None = None
    output_sha256: str | None = None
    output_preview: str | None = None
    tool_status: str | None = None
    tool_error: str | None = None
    server_reported_s: float | None = Field(
        None, description="Time the tool server measured itself, joined in from its logs"
    )

    text_chars: int | None = None
    summary_text: str | None = Field(None, description="Reasoning summary, when the platform returns one")


class EvaluationRecord(_Record):
    """One evaluator's verdict on one attempt."""

    eval_id: str
    attempt_id: str
    run_id: str
    arm: Arm
    question_id: str
    repeat_index: int = 0

    evaluator: str
    evaluator_kind: EvaluatorKind
    category: EvaluatorCategory
    evaluator_version: str | None = None
    status: EvaluationStatus = "ok"
    score: float | None = None
    passed: bool | None = None
    label: str | None = None
    threshold: float | None = None
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict, description="E.g. per-criterion results")

    judge_model: str | None = None
    judge_input_tokens: int | None = Field(None, description="Kept apart from agent tokens")
    judge_output_tokens: int | None = None
    duration_s: float | None = None
    evaluated_at: datetime | None = None
    foundry_eval_id: str | None = None
    foundry_eval_run_id: str | None = None
    foundry_output_item_id: str | None = None
    human_reviewed: bool = False


class TraceRecord(_Record):
    """What Foundry's own server-side trace (Application Insights) says about one attempt.

    Joined after the run, because spans take minutes to appear. It is a cross-check on the stream:
    the tool span reports success even when the call failed, so the agent span is the signal.
    """

    attempt_id: str
    run_id: str
    arm: Arm
    response_id: str
    trace_id: str = Field(description="Application Insights operation id for the whole invocation")
    agent_span_success: bool | None = None
    agent_result_code: str | None = None
    error_type: str | None = Field(None, description="Foundry's error.type on the agent span, e.g. tool_user_error")
    agent_span_s: float | None = Field(None, description="Server-side duration of the agent invocation")
    tool_spans: int = 0
    tool_names: list[str] = Field(default_factory=list)
    tool_time_s: float | None = Field(
        None, description="Server-side execution time of the tool calls from the trace; the stream cannot show it"
    )
    tool_span_s: list[float] = Field(default_factory=list, description="Duration of each tool span, in call order")
    tool_error: bool = Field(
        False, description="The trace shows a tool error: error.type starts with tool, or a tool span failed"
    )
    model_spans: int = Field(0, description="Model-call (chat) spans; Foundry may not emit one for every model call")
    model_time_s: float | None = Field(None, description="Wall time covered by the model-call spans")
    untraced_s: float | None = Field(
        None, description="Agent span time covered by no tool or model span: orchestration and untraced model calls"
    )
    model_input_tokens: int | None = Field(None, description="Sum over model-call spans")
    model_output_tokens: int | None = None
    model_cache_read_tokens: int | None = Field(
        None, description="Prompt-cache hits from the trace; the Responses API usage reports 0 here"
    )
    model_cache_creation_tokens: int | None = None
    spans: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tool and model-call spans in start order: kind, name, start_s (from agent start), duration_s, "
        "success, and token counts for model calls",
    )
    fetched_at: datetime | None = None


class ToolTimingRecord(_Record):
    """How long one tool call took inside the tool server, by its own clock, joined from the server's log.

    Only the arm 3 proxy writes this log; it splits a call into the blocks the Foundry trace cannot see.
    """

    attempt_id: str
    run_id: str
    arm: Arm
    step_index: int | None = Field(None, description="StepRecord.index of the tool call")
    tool_name: str
    source: str = Field(description="Which log the timing came from")
    started_at: datetime | None = None
    total_s: float | None = Field(None, description="Request received to answer returned, inside the server")
    sign_in_s: float | None = Field(None, description="Getting the upstream token (cached after the first call)")
    session_open_s: float | None = Field(None, description="Opening the upstream MCP session")
    tool_discovery_s: float | None = Field(None, description="Listing upstream tools; first call only")
    upstream_call_s: float | None = Field(None, description="The upstream service's own work: the Data Agent answering")
    session_close_s: float | None = None
    is_error: bool | None = None
    answer_chars: int | None = None


TABLES: dict[str, type[_Record]] = {
    "runs": RunRecord,
    "attempts": AttemptRecord,
    "steps": StepRecord,
    "evaluations": EvaluationRecord,
    "traces": TraceRecord,
    "tool_timings": ToolTimingRecord,
}
