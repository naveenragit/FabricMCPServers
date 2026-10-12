"""Turn a Foundry agent response, streamed or not, into an AttemptRecord and its StepRecords.

Per-step timing needs the stream: a non-streaming response carries only totals, so the time split
(thinking vs tool vs everything else) is recorded only when events were observed. Token usage is
reported once per response; the platform runs the model/tool loop server side and does not break
usage down per model call.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .records import Arm, AttemptRecord, AttemptStatus, StepKind, StepRecord, StepTimingBasis

QUERY_KEYS = ("query", "dax", "sql", "userQuestion", "question")
_INFRA_HINTS = (
    "timeout",
    "connection",
    "ratelimit",
    "rate_limit",
    "server_error",
    "internalserver",
    "unavailable",
)


def _as_dict(obj: Any) -> Any:
    if obj is None or isinstance(obj, str | int | float | bool | dict | list):
        return obj
    for name in ("model_dump", "to_dict"):
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn(mode="json") if name == "model_dump" else fn()
    return vars(obj)


def _event_type(event: Any) -> str:
    kind = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
    return kind or ""


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _error_text(value: Any) -> str | None:
    if isinstance(value, dict):
        return _as_text(value.get("message") or value)
    return _as_text(value)


def _query_text(arguments: str | None) -> str | None:
    if not arguments:
        return None
    try:
        parsed = json.loads(arguments)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        for key in QUERY_KEYS:
            if isinstance(parsed.get(key), str):
                return parsed[key]
    return None


def _looks_infra(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status >= 500 or status == 429):
        return True
    label = f"{type(exc).__name__} {exc}".lower()
    return any(hint in label for hint in _INFRA_HINTS)


@dataclass
class _Timing:
    added: float | None = None
    done: float | None = None
    args_done: float | None = None
    in_progress: float | None = None
    ended: float | None = None


def _span(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, end - start)


class AttemptRecorder:
    """Create it just before sending the request so the clock covers submission latency."""

    def __init__(
        self,
        *,
        run_id: str,
        arm: Arm,
        question_id: str,
        question: str,
        repeat_index: int = 0,
        schema_tools: Iterable[str] = (),
        preview_chars: int = 2000,
        clock: Callable[[], float] = time.perf_counter,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.attempt_id = uuid.uuid4().hex
        self._run_id, self._arm = run_id, arm
        self._question_id, self._question, self._repeat = question_id, question, repeat_index
        self._schema_tools = frozenset(schema_tools)
        self._preview = preview_chars
        self._clock, self._now = clock, now
        self._t0 = clock()
        self._started_at = now()
        self._timings: dict[int, _Timing] = {}
        self._item_index: dict[str, int] = {}
        self._streamed_items: dict[int, dict[str, Any]] = {}
        self._first_event: float | None = None
        self._first_token: float | None = None
        self._t_end: float | None = None
        self._response: dict[str, Any] | None = None
        self._error: dict[str, Any] | None = None
        self._exception: tuple[str, str, bool, bool] | None = None
        self._streamed = False

    def _elapsed(self) -> float:
        return self._clock() - self._t0

    def _timing(self, event: dict[str, Any]) -> _Timing | None:
        index = event.get("output_index")
        if index is None:
            index = self._item_index.get(event.get("item_id", ""))
        return None if index is None else self._timings.setdefault(index, _Timing())

    def on_event(self, event: Any) -> None:
        """Feed every streamed event in arrival order."""
        kind = _event_type(event)
        t = self._elapsed()
        self._streamed = True
        if self._first_event is None:
            self._first_event = t
        if kind == "response.output_text.delta":
            if self._first_token is None:
                self._first_token = t
            return
        if not kind.startswith(("response.", "error")):
            return
        data = _as_dict(event)
        if kind in ("response.output_item.added", "response.output_item.done"):
            index = data["output_index"]
            item = _as_dict(data["item"])
            timing = self._timings.setdefault(index, _Timing())
            self._streamed_items[index] = item
            if item.get("id"):
                self._item_index[item["id"]] = index
            if kind.endswith("added"):
                timing.added = t
            else:
                timing.done = t
        elif kind == "response.mcp_call_arguments.done":
            if timing := self._timing(data):
                timing.args_done = t
        elif kind in ("response.mcp_call.in_progress", "response.mcp_list_tools.in_progress"):
            if timing := self._timing(data):
                timing.in_progress = t
        elif kind in (
            "response.mcp_call.completed",
            "response.mcp_call.failed",
            "response.mcp_list_tools.completed",
            "response.mcp_list_tools.failed",
        ):
            if timing := self._timing(data):
                timing.ended = t
        elif kind in ("response.completed", "response.incomplete", "response.failed"):
            self._response = _as_dict(data.get("response"))
            self._t_end = t
        elif kind == "error":
            self._error = {"code": data.get("code"), "message": data.get("message")}

    def record_response(self, response: Any, *, elapsed_s: float) -> None:
        """Non-streaming path: totals only, no step timing."""
        self._response = _as_dict(response)
        self._t_end = elapsed_s

    def fail(self, exc: BaseException, *, infra: bool | None = None) -> None:
        """Record an exception raised while sending or reading the response."""
        timed_out = isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
        self._exception = (
            type(exc).__name__,
            str(exc),
            timed_out,
            _looks_infra(exc) if infra is None else infra,
        )

    def build(self) -> tuple[AttemptRecord, list[StepRecord]]:
        duration = self._t_end if self._t_end is not None else self._elapsed()
        response = self._response or {}
        # The final response is authoritative; streamed items cover a stream that ended without one.
        if response.get("output"):
            indexed = list(enumerate(response["output"]))
        else:
            indexed = sorted(self._streamed_items.items())
        items = [_as_dict(item) for _, item in indexed]
        steps = [
            self._step(pos, item, self._timings.get(idx, _Timing()))
            for pos, ((idx, _), item) in enumerate(zip(indexed, items, strict=True))
        ]
        answer = "".join(self._message_text(i) for i in items if i.get("type") == "message") or None
        return self._attempt(response, steps, duration, answer), steps

    def _step(self, index: int, item: dict[str, Any], timing: _Timing) -> StepRecord:
        item_type = item.get("type")
        kind: StepKind = {
            "reasoning": "reasoning",
            "mcp_list_tools": "tool_list",
            "mcp_call": "tool_call",
            "message": "message",
        }.get(item_type, "other")
        basis: StepTimingBasis = "none"
        start = end = None
        if kind == "tool_call":
            # Arguments are final once args_done fires; anything earlier would count argument generation as tool time.
            if timing.args_done is not None:
                start, basis = timing.args_done, "args_done"
            elif timing.in_progress is not None:
                start, basis = timing.in_progress, "in_progress"
            else:
                start, basis = timing.added, "item_span"
            end = timing.ended if timing.ended is not None else timing.done
        elif kind == "tool_list":
            start = timing.in_progress if timing.in_progress is not None else timing.added
            end = timing.ended if timing.ended is not None else timing.done
            basis = "in_progress" if timing.in_progress is not None else "item_span"
        else:
            start, end = timing.added, timing.done
            basis = "item_span"
        if start is None or end is None:
            basis = "none"
        step = StepRecord(
            step_id=f"{self.attempt_id}:{index}",
            attempt_id=self.attempt_id,
            run_id=self._run_id,
            arm=self._arm,
            index=index,
            kind=kind,
            item_type=item_type,
            start_s=start,
            duration_s=_span(start, end),
            timing_basis=basis,
        )
        if kind == "tool_call":
            arguments = _as_text(item.get("arguments"))
            output = _as_text(item.get("output"))
            error = _error_text(item.get("error"))
            step.server_label = item.get("server_label")
            step.tool_name = item.get("name")
            step.is_schema_call = step.tool_name in self._schema_tools
            step.arguments = arguments
            step.query_text = _query_text(arguments)
            step.tool_status = item.get("status")
            step.tool_error = error
            if output is not None:
                step.output_chars = len(output)
                step.output_sha256 = hashlib.sha256(output.encode()).hexdigest()
                step.output_preview = output[: self._preview]
        elif kind == "tool_list":
            step.server_label = item.get("server_label")
            step.tool_error = _error_text(item.get("error"))
        elif kind == "message":
            step.text_chars = len(self._message_text(item))
        elif kind == "reasoning":
            summary = " ".join(p.get("text", "") for p in item.get("summary") or [] if isinstance(p, dict))
            step.summary_text = summary or None
        return step

    @staticmethod
    def _message_text(item: dict[str, Any]) -> str:
        parts = item.get("content") or []
        return "".join(
            p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "output_text"
        )

    def _attempt(
        self, response: dict[str, Any], steps: list[StepRecord], duration: float, answer: str | None
    ) -> AttemptRecord:
        calls = [s for s in steps if s.kind == "tool_call"]
        timed = self._streamed and any(s.timing_basis != "none" for s in steps)
        tool_time = sum(s.duration_s or 0.0 for s in calls)
        list_time = sum(s.duration_s or 0.0 for s in steps if s.kind == "tool_list")
        think_time = sum(s.duration_s or 0.0 for s in steps if s.kind == "reasoning")
        usage = response.get("usage") or {}
        in_details = usage.get("input_tokens_details") or {}
        out_details = usage.get("output_tokens_details") or {}

        status: AttemptStatus
        error_type = error_message = incomplete_reason = None
        infra = False
        api_status = response.get("status")
        if self._exception:
            name, message, timed_out, infra = self._exception
            status, error_type, error_message = ("timeout" if timed_out else "failed"), name, message
        elif api_status == "completed":
            status = "completed"
        elif api_status == "incomplete":
            status = "incomplete"
            incomplete_reason = (response.get("incomplete_details") or {}).get("reason")
        else:
            status = "failed"
            err = response.get("error") or self._error or {}
            error_type = err.get("code") or (api_status or "no_final_response")
            error_message = err.get("message")
            infra = any(h in str(error_type).lower() for h in _INFRA_HINTS)

        agent = response.get("agent") or {}
        conversation = response.get("conversation")
        return AttemptRecord(
            attempt_id=self.attempt_id,
            run_id=self._run_id,
            arm=self._arm,
            question_id=self._question_id,
            repeat_index=self._repeat,
            question=self._question,
            answer_text=answer,
            status=status,
            error_type=error_type,
            error_message=error_message,
            incomplete_reason=incomplete_reason,
            infra_error=infra,
            started_at=self._started_at,
            ended_at=self._now(),
            duration_s=duration,
            timing_basis="stream" if timed else "total_only",
            time_to_first_event_s=self._first_event,
            time_to_first_token_s=self._first_token,
            reasoning_time_s=think_time if timed else None,
            tool_time_s=tool_time if timed else None,
            tool_list_time_s=list_time if timed else None,
            model_other_s=max(0.0, duration - tool_time - list_time - think_time) if timed else None,
            response_id=response.get("id"),
            conversation_id=conversation.get("id") if isinstance(conversation, dict) else conversation,
            model=response.get("model"),
            agent_name=agent.get("name") if isinstance(agent, dict) else None,
            agent_version=agent.get("version") if isinstance(agent, dict) else None,
            input_tokens=usage.get("input_tokens"),
            cached_input_tokens=in_details.get("cached_tokens"),
            cache_write_tokens=in_details.get("cache_write_tokens"),
            output_tokens=usage.get("output_tokens"),
            reasoning_tokens=out_details.get("reasoning_tokens"),
            total_tokens=usage.get("total_tokens"),
            tool_calls=len(calls),
            tool_calls_failed=sum(1 for s in calls if s.tool_error or s.tool_status == "failed"),
            tool_list_calls=sum(1 for s in steps if s.kind == "tool_list"),
            schema_calls=sum(1 for s in calls if s.is_schema_call),
            query_calls=sum(1 for s in calls if not s.is_schema_call),
            answer_chars=len(answer) if answer else None,
        )
