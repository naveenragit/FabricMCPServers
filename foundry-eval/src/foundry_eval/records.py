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

    # Roll-ups, filled in by RunStore.finish()
    n_attempts: int = 0
    n_completed: int = 0
    n_failed: int = 0
    n_infra_errors: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    tool_time_s: float = 0.0
    reasoning_time_s: float = 0.0
    attempts_duration_s: float = Field(
        0.0, description="Sum of attempt durations (differs from wall time when concurrent)"
    )


class AttemptRecord(_Record):
    """One agent call: a question asked once, on one arm."""

    attempt_id: str
    run_id: str
    arm: Arm
    question_id: str
    repeat_index: int = 0
    question: str
    answer_text: str | None = None

    status: AttemptStatus
    error_type: str | None = None
    error_message: str | None = None
    incomplete_reason: str | None = None
    infra_error: bool = Field(
        False, description="The platform failed, not the agent; exclude from quality comparisons"
    )

    started_at: datetime
    ended_at: datetime
    duration_s: float = Field(description="Client wall clock, request sent to final event")
    timing_basis: TimingBasis
    time_to_first_event_s: float | None = None
    time_to_first_token_s: float | None = None
    reasoning_time_s: float | None = Field(None, description="Model thinking, from reasoning item spans")
    tool_time_s: float | None = Field(None, description="MCP tool calls, arguments final to result")
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
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = Field(None, description="Includes reasoning tokens")
    reasoning_tokens: int | None = None
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


TABLES: dict[str, type[_Record]] = {
    "runs": RunRecord,
    "attempts": AttemptRecord,
    "steps": StepRecord,
    "evaluations": EvaluationRecord,
}
