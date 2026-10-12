"""Evaluation dataset rows (plan section 5). Reference answers live only in the git-ignored dataset file."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .numbers import Num, extract_numbers

CriterionKind = Literal["fact", "trap", "behavior", "support"]
ExpectedKind = Literal["count", "amount", "percent", "number"]


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    instruction: str
    kind: CriterionKind = "fact"
    weight: float = Field(1.0, description="Support criteria weigh 0 for correctness")
    numeric: bool = Field(True, description="False when the numbers in the text are not values to match")


class Expected(BaseModel):
    """One number a fact criterion asks for, with the tolerance the criterion states."""

    model_config = ConfigDict(extra="forbid")
    criterion: str
    value: float
    kind: ExpectedKind
    tol_abs: float = 0.0
    tol_rel: float = 0.0
    sign_free: bool = False
    unit: float = Field(0.0, description="Display unit of the number in the criterion; half of it is the implied tolerance")


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    set: Literal["core", "risk"]
    category: str
    tier: int | None = None
    query: str
    ground_truth: str | None = None
    expected: list[Expected] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)
    expected_behavior: str | None = None
    must_not_call: list[str] = Field(default_factory=list)
    applies_to: list[Literal["dax", "sql", "fabric"]] = Field(default_factory=lambda: ["dax", "sql", "fabric"])
    gate: Literal["zero", "report"] = "report"
    checks: list[str] = Field(default_factory=list)
    split: Literal["dev", "test"] = "test"
    answerable: bool = Field(True, description="The row has a data answer, so the data tool must be called")


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ID = re.compile(r"\b[A-Z]{2,6}-\d+\b")
_QUARTER = re.compile(r"\bQ[1-4]\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_COUNT_NOUN = re.compile(r"\b(?:each of the \d+|top \d+|\d+ (?:clients?|accounts?|advisors?) (?:on file|at least))", re.IGNORECASE)
_TOL_REL_PCT = re.compile(r"within\s+([\d.]+)\s*%\s*relative")
_TOL_PP = re.compile(r"within\s+([\d.]+)\s*percentage points?")
_TOL_PCT = re.compile(r"within\s+([\d.]+)\s*%")
_TOL_ABS = re.compile(r"within\s+([\d.]+)(?![\d.]*\s*%)")
_TOLERANCE_TEXT = re.compile(r"within\s+[\d.]+\s*(?:%|percentage points?)?(?:\s+relative)?")
_AVOID = re.compile(r"\b(?:not|instead of|rather than|such as)\b", re.IGNORECASE)


def _stated_tolerance(text: str) -> tuple[float, float]:
    if m := _TOL_REL_PCT.search(text):
        return 0.0, float(m.group(1)) / 100
    if m := _TOL_PP.search(text):
        return float(m.group(1)), 0.0
    if m := _TOL_PCT.search(text):
        return 0.0, float(m.group(1)) / 100
    if m := _TOL_ABS.search(text):
        return float(m.group(1)), 0.0
    return 0.0, 0.0


def _kind(num: Num) -> ExpectedKind:
    if num.percent:
        return "percent"
    if num.money:
        return "amount"
    return "count" if num.decimals == 0 and not num.scaled else "number"


def _dedupe_scales(numbers: list[Num]) -> list[Num]:
    """'$22.44 billion ($22,437,157,528.33)' is one figure shown twice; keep the more precise."""
    kept: list[Num] = []
    for num in numbers:
        twin = next((k for k in kept if k.value and abs(k.value - num.value) <= 1e-3 * abs(k.value)), None)
        if twin is None:
            kept.append(num)
        elif num.unit < twin.unit:
            kept[kept.index(twin)] = num
    return kept


def derive_expected(criterion: Criterion) -> list[Expected]:
    """Read the numbers and the tolerance out of a fact criterion's instruction."""
    if criterion.kind != "fact" or not criterion.numeric:
        return []
    text = criterion.instruction
    tol_abs, tol_rel = _stated_tolerance(text)
    # Text after "not" or "such as" names values to avoid, not values to match.
    wanted = _AVOID.split(text)[0]
    scrubbed = _COUNT_NOUN.sub(" ", _TOLERANCE_TEXT.sub(" ", _QUARTER.sub(" ", _ID.sub(" ", _DATE.sub(" ", wanted)))))
    scrubbed = _YEAR.sub(" ", scrubbed)
    sign_free = "may be shown as negative" in text.lower() or "negative or as a positive" in text.lower()
    return [
        Expected(
            criterion=criterion.id,
            value=num.value,
            kind=_kind(num),
            tol_abs=tol_abs,
            tol_rel=tol_rel,
            sign_free=sign_free and num.money,
            unit=0.0 if _kind(num) == "count" else num.unit,
        )
        for num in _dedupe_scales(extract_numbers(scrubbed))
    ]


def with_expected(row: Row) -> Row:
    row.expected = [e for c in row.criteria for e in derive_expected(c)]
    return row


def load_dataset(path: Path | str) -> list[Row]:
    with Path(path).open(encoding="utf-8") as fh:
        return [Row.model_validate_json(line) for line in fh if line.strip()]


def dataset_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_dataset(path: Path | str, rows: list[Row]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row.model_dump(), ensure_ascii=False) + "\n")
