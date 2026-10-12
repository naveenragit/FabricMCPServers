"""Number extraction and the D3 tolerance rule: |response - expected| <= max(stated tolerance, 0.5 * u).

u is the unit of the last digit the response displays after unit scaling, so "$22.44 billion" has
u = 0.01 billion. Stdlib only, so the same code can be registered as a Foundry code-based evaluator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCALES = {
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mm": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}  # fmt: skip

# Dates, identifiers and quarter labels are not figures; blank them so their digits are not read.
_NOISE = re.compile(
    r"\d{4}-\d{2}-\d{2}|\b[A-Z]{2,6}-\d+\b|\b(?:FUND|CD-?)\d+\b|\bQ[1-4]\b|\b(?:19|20)\d{2}\b(?=[ ,.)\-/]|$)",
    re.IGNORECASE,
)
_NUMBER = re.compile(
    r"""
    (?<![\w])
    (?P<neg1>[-\u2212\u2013])?
    (?P<cur>\$)?
    (?P<neg2>[-\u2212\u2013])?
    (?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    (?P<pct>\s?%)?
    (?:\s?(?P<scale>trillion|billion|million|thousand|bn|mm|tn)\b|(?P<abbr>[BMKT])\b)?
    """,
    re.VERBOSE | re.IGNORECASE,
)


@dataclass(frozen=True)
class Num:
    value: float
    unit: float
    text: str
    percent: bool
    money: bool
    scaled: bool
    decimals: int
    sigfigs: int

    @property
    def abbreviated(self) -> bool:
        return self.scaled or self.decimals > 0


def _sigfigs(digits: str) -> int:
    core = digits.replace(",", "").replace(".", "").lstrip("0")
    return len(core.rstrip("0")) if "." not in digits else len(core)


def extract_numbers(text: str) -> list[Num]:
    """Every figure in the text, with the value, display unit and significant figures."""
    cleaned = _NOISE.sub(lambda m: " " * len(m.group()), text or "")
    found: list[Num] = []
    for match in _NUMBER.finditer(cleaned):
        digits = match.group("digits")
        scale_word = (match.group("scale") or match.group("abbr") or "").lower()
        factor = _SCALES.get(scale_word, 1.0)
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        value = float(digits.replace(",", "")) * factor
        if match.group("neg1") or match.group("neg2"):
            value = -value
        found.append(
            Num(
                value=value,
                unit=10 ** (-decimals) * factor,
                text=match.group().strip(),
                percent=bool(match.group("pct")),
                money=bool(match.group("cur")),
                scaled=bool(scale_word),
                decimals=decimals,
                sigfigs=_sigfigs(digits),
            )
        )
    return found


def decimals_of(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


@dataclass(frozen=True)
class Want:
    """What a fact criterion expects: one number and its stated tolerance."""

    value: float
    kind: str  # count | amount | percent | number
    tol_abs: float = 0.0
    tol_rel: float = 0.0
    sign_free: bool = False
    unit: float = 0.0  # display unit in the criterion; with no stated tolerance, half of it is implied


def tolerance(want: Want, got: Num) -> float:
    stated = max(want.tol_abs, want.tol_rel * abs(want.value))
    implied = 0.5 * want.unit if stated == 0 else 0.0
    return max(stated, implied, 0.5 * got.unit)


def matches(want: Want, got: Num) -> bool:
    """D3: counts shown as full integers match exactly; abbreviated or decimal displays use the half-unit rule."""
    expected = abs(want.value) if want.sign_free else want.value
    value = abs(got.value) if want.sign_free else got.value
    if want.kind == "count":
        if not got.abbreviated and not got.percent:
            return value == expected
        return abs(value - expected) <= tolerance(want, got)
    if want.kind == "percent":
        if got.percent:
            return abs(value - expected) <= tolerance(want, got)
        # A bare fraction such as 0.5971 stands for 59.71%.
        return abs(value) <= 1.5 and abs(value * 100 - expected) <= max(want.tol_abs, want.tol_rel * abs(expected), 0.5 * got.unit * 100)
    if got.percent:
        return False
    return abs(value - expected) <= tolerance(want, got)


def find_match(want: Want, numbers: list[Num]) -> Num | None:
    return next((n for n in numbers if matches(want, n)), None)


def low_precision(got: Num) -> bool:
    """D3 guard: a rounded figure with fewer than 3 significant figures goes to manual review, not auto-pass."""
    return got.sigfigs < 3 and got.abbreviated or (got.percent and got.sigfigs < 3)
