"""Evaluator plumbing: the case an evaluator sees, the outcome it returns, and shared configuration."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ..dataset import Row
from ..records import AttemptRecord, EvaluatorCategory, EvaluatorKind, StepRecord

Status = Literal["ok", "skipped", "error"]


@dataclass
class EvalConfig:
    """Facts about the deployment that the checks need; all of it comes from .env, none is committed."""

    canary: str = ""
    identifiers: list[str] = field(default_factory=list)
    model_id: str | None = None
    named_row_cap: int = 50
    allowed_tools: dict[str, list[str]] = field(default_factory=dict)
    schema_tools: dict[str, list[str]] = field(default_factory=dict)
    server_labels: dict[str, str] = field(default_factory=dict)


@dataclass
class Case:
    row: Row
    attempt: AttemptRecord
    steps: list[StepRecord]
    config: EvalConfig

    @property
    def arm(self) -> str:
        return self.attempt.arm

    @property
    def answer(self) -> str:
        return self.attempt.answer_text or ""

    @property
    def calls(self) -> list[StepRecord]:
        return [s for s in self.steps if s.kind == "tool_call"]

    @property
    def query_calls(self) -> list[StepRecord]:
        schema = set(self.config.schema_tools.get(self.arm, []))
        return [s for s in self.calls if s.tool_name not in schema]

    def tool_outputs(self) -> list[str]:
        return [s.output_preview for s in self.calls if s.output_preview]


@dataclass
class Outcome:
    score: float | None
    reason: str
    passed: bool | None = None
    status: Status = "ok"
    label: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    judge_input_tokens: int | None = None
    judge_output_tokens: int | None = None


def skipped(reason: str) -> Outcome:
    return Outcome(score=None, reason=reason, status="skipped")


@dataclass(frozen=True)
class Evaluator:
    name: str
    kind: EvaluatorKind
    category: EvaluatorCategory
    run: Callable[[Case], Outcome]
    version: str = "1"
    zero_tolerance: bool = False


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a model reply, tolerating code fences and surrounding prose."""
    match = _JSON_OBJECT.search(text or "")
    if not match:
        return None
    try:
        value = json.loads(match.group())
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
