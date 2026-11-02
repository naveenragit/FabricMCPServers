"""The evaluator set and the configuration it needs from the environment."""

from __future__ import annotations

import os
import re

from ..agents import ARMS
from .base import EvalConfig, Evaluator
from .deterministic import DETERMINISTIC
from .gates import BEHAVIOR, ZERO_TOLERANCE, gate_status
from .judges import Judge, behavior_review, make_behavior_judge, make_criteria_judge
from .runner import evaluate_attempt, evaluate_run

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

SCHEMA_TOOLS = {"dax": ["get_semantic_model_schema"], "sql": ["get_database_schema"], "fabric": []}
SCORING_MODES = ("judge", "deterministic")
ACCURACY = ("criteria_judge", "criteria_check")


def config_from_env(env: dict[str, str] | None = None) -> EvalConfig:
    values = dict(os.environ if env is None else env)
    identifiers = [v for v in values.values() if _GUID.match(v)]
    return EvalConfig(
        canary=values.get("EVAL_CANARY", ""),
        identifiers=identifiers,
        model_id=values.get("SEMANTIC_MODEL_ID") or None,
        named_row_cap=int(values.get("EVAL_NAMED_ROW_CAP", "50")),
        allowed_tools={key: list(arm.allowed_tools) for key, arm in ARMS.items()},
        schema_tools=SCHEMA_TOOLS,
        server_labels={key: arm.server_label for key, arm in ARMS.items()},
    )


def all_evaluators(judge: Judge) -> list[Evaluator]:
    return [*DETERMINISTIC, make_criteria_judge(judge), make_behavior_judge(judge)]


def deterministic_evaluators() -> list[Evaluator]:
    """No LLM judge: code-only checks, with undecided criteria and behaviors marked needs_review."""
    return [*DETERMINISTIC, make_criteria_judge(None), Evaluator("behavior_review", "human", "risk", behavior_review)]


def evaluators_for(mode: str, judge: Judge | None = None) -> list[Evaluator]:
    if mode == "deterministic":
        return deterministic_evaluators()
    if judge is None:
        raise ValueError("judge scoring needs a judge")
    return all_evaluators(judge)


__all__ = [
    "ACCURACY", "BEHAVIOR", "SCORING_MODES", "ZERO_TOLERANCE", "EvalConfig", "Evaluator", "all_evaluators", "config_from_env",
    "deterministic_evaluators", "evaluate_attempt", "evaluate_run", "evaluators_for", "gate_status",
]
