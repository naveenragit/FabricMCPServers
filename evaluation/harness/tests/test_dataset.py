"""Dataset rows: one category carries the question type and tier; older dataset files still load."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from foundry_eval.dataset import Row, upgrade_row


def test_category_gives_type_and_tier():
    assert (Row(id="q", category="Core Tier 3", query="?").type, Row(id="q", category="Core Tier 3", query="?").tier) == ("core", 3)
    assert (Row(id="q", category="Core Relationship", query="?").tier) is None
    assert Row(id="r", category="Risk Prompt injection", query="?").type == "risk"
    with pytest.raises(ValidationError):
        Row(id="q", category="tier3", query="?")


def test_older_rows_upgrade():
    old = {"id": "Q1", "set": "core", "category": "tier2", "tier": 2, "query": "?", "split": "dev", "applies_to": ["dax"],
           "checks": [], "criteria": [{"id": "c", "instruction": "x", "kind": "support", "weight": 0}]}
    row = Row.model_validate(upgrade_row(old))
    assert (row.category, row.criteria[0].weight) == ("Core Tier 2", 0.0)
    assert Row.model_validate(upgrade_row({"id": "E1", "set": "core", "category": "edge", "query": "?"})).category == "Risk Edge case"
    assert Row.model_validate(upgrade_row({"id": "R1", "type": "risk", "category": "Robustness", "query": "?"})).category == "Risk Robustness"
