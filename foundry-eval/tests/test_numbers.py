"""Number extraction, the D3 tolerance rule, and expected-value derivation."""

from __future__ import annotations

import pytest

from foundry_eval.dataset import Criterion, derive_expected
from foundry_eval.numbers import Want, extract_numbers, find_match, low_precision


def want(value, kind="amount", **kw):
    return Want(value, kind, **kw)


def hit(w, text):
    return find_match(w, extract_numbers(text)) is not None


def test_q10_rounded_billions_pass_under_the_half_unit_rule():
    w = want(22_437_157_528.33, tol_rel=0.0001)
    assert hit(w, "Total AUM is $22.44 billion as of the latest snapshot")
    assert hit(w, "AUM: $22,437,157,528.33")
    assert not hit(w, "AUM is $22.5 billion")
    assert not hit(w, "AUM is $22,400,000,000")
    assert hit(w, "AUM is $22,437,157,000"), "within the stated 0.01%"


def test_counts_shown_as_full_integers_match_exactly():
    w = want(3000, "count")
    assert hit(w, "We have 3,000 clients")
    assert not hit(w, "We have 3,001 clients")
    assert hit(w, "about 3.0 thousand clients")


def test_percentages_use_the_stated_tolerance_and_the_display_precision():
    w = want(59.71, "percent", tol_abs=0.05)
    assert hit(w, "59.71% were accepted")
    assert hit(w, "about 59.7%")
    assert not hit(w, "about 61%")
    assert hit(want(61.47, "percent", tol_abs=0.05), "The win rate is 0.6147")


def test_sign_free_amounts_accept_a_negative_or_a_positive_outflow():
    w = want(13_940_514_764.66, tol_rel=0.0001, sign_free=True)
    assert hit(w, "Sell volume was -$13,940,514,764.66")
    assert hit(w, "Sell volume $13,940,514,764.66")
    assert not hit(want(13_940_514_764.66, tol_rel=0.0001), "Sell volume was -$13,940,514,764.66")


def test_dates_ids_and_quarters_are_not_read_as_figures():
    texts = [n.text for n in extract_numbers("As of 2025-12-26, client CLT-002378 in Q4 2025 had 6 accounts")]
    assert texts == ["6"]


def test_thousands_separators_ranges_and_percent_signs():
    nums = extract_numbers("8-12 interactions: 1,687 clients, 61.74%")
    assert [n.value for n in nums] == [8, 12, 1687, 61.74]
    assert nums[-1].percent


def test_fewer_than_three_significant_figures_is_flagged_not_passed():
    [n] = extract_numbers("AUM is $22 billion")
    assert low_precision(n)
    [n] = extract_numbers("about 60%")
    assert low_precision(n)
    [n] = extract_numbers("AUM is $22.44 billion")
    assert not low_precision(n)
    [n] = extract_numbers("We have 12 advisors")
    assert not low_precision(n), "a full integer is exact, not rounded"


@pytest.mark.parametrize(
    ("text", "values", "kinds"),
    [
        ("Response must state that there are 3,000 clients.", [3000], ["count"]),
        ("must state total AUM of about $22.44 billion ($22,437,157,528.33, within 0.01%).", [22_437_157_528.33], ["amount"]),
        ("must identify 2025-12-26 (the latest weekly snapshot) as the as-of date.", [], []),
        ("must report a win rate of about 61.47% (within 0.05 percentage points).", [61.47], ["percent"]),
        ("must list, in order, CLT-002378, CLT-000918 and CLT-000051.", [], []),
    ],
)
def test_expected_values_are_derived_from_the_criterion_text(text, values, kinds):
    got = derive_expected(Criterion(id="c", instruction=text))
    assert [e.value for e in got] == pytest.approx(values)
    assert [e.kind for e in got] == kinds


def test_tolerance_is_read_from_the_criterion():
    [e] = derive_expected(Criterion(id="c", instruction="about 61.47% (within 0.05 percentage points)"))
    assert e.tol_abs == 0.05 and e.tol_rel == 0
    [e] = derive_expected(Criterion(id="c", instruction="about $475,248,296.08 (within 0.01%)"))
    assert e.tol_rel == pytest.approx(0.0001)


def test_trap_and_behavior_criteria_have_no_expected_values():
    assert derive_expected(Criterion(id="t", instruction="must not report $602,555,151.28", kind="trap")) == []
