# Shared Evaluation Dataset

## Canonical Source

[wealth-management-benchmark.jsonl](wealth-management-benchmark.jsonl) is the authoritative dataset for all three arms: DAX semantic model, SQL lakehouse, and Fabric data agent. Edit prompts, reference answers, criteria, and expected behavior here, not in an arm-specific copy or the historical Markdown tables.

Dataset revision: **v7, 2026-11-05**. It contains **102 rows**:

- Q01-Q50: 50 factual questions (Q41 reworded in v3 for model v2).
- Q51-Q58: eight `relationship` questions added in v4.
- E01-E05: five revised edge-case prompts/rubrics.
- R01-R29: 29 revised risk prompts/rubrics.
- R30-R39: ten additional single-turn risk and positive-control cases.

v5 removed the dev/test `split` field. v6 made the edge cases risk questions and removed fields that never changed a result: `applies_to` (every row runs on every arm), `checks` (every evaluator runs on every row), and the criterion `weight` (support criteria never count; all others count equally). v7 folds question type and tier into one field, `category`: `Core Tier 1`-`Core Tier 5` and `Core Relationship` (Q01-Q58, the business use case), and `Risk <theme>` (R01-R39, and E01-E05 as `Risk Edge case`).

All rows retain the existing `foundry_eval.dataset.Row` schema and apply to `dax`, `sql`, and `fabric`. Every row runs on every arm; there is no dev/test split (removed 2026-11-05). Reference answers remain local in ignored JSONL files.

## Validation

Run from the repository root using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe scripts/evaluation/validate_evaluation_dataset.py
```

This command **validates without rewriting** the canonical dataset. It checks schema compatibility, unique row/criterion IDs, all-three-arm applicability, nonempty prompts/rubrics, and numeric-expectation links. It prints row counts, splits, and the dataset hash.

The legacy `wealth-management-eval.jsonl`, the answer-key overrides, the old risk-question Markdown tables, and `foundry_eval.build_dataset.build_dataset` are historical inputs, not active dataset-generation sources. Do not regenerate the canonical file from them.

## Revision Scope

The revised rows remove unsolicited exact-date/statistic requirements, distinguish safety from optional helpfulness, make R02 intent-based across query languages, allow smaller platform caps, label R23 as identity transparency, and stop demanding R28 recovery after a platform-aborted tool response. R15 explicitly requests the IDs that its stored ranking reference grades; only the first-ten prefix has an independent reference, not the full returned ranking.

New cases cover single-response batching and encoded disclosure bypasses, external-link disclosure requests, a false factual premise, demographic relevance, unsupported audit assurances, a user-stated tool-call budget, a Spanish count question, requests to guess without tools, and deliberate snapshot mislabeling. All are runnable with the existing single-turn tool surface; no new stub, credential, tool, or policy is introduced.

R12 still requires the existing injection stub. It tests the outer Foundry agent, including for the Fabric arm, not Fabric's internal model or actual network exfiltration.

## Coverage Boundaries

This is not a claim that every LLM risk is covered. Supply-chain integrity, training/data poisoning, downstream output execution, vector retrieval isolation, authenticated per-user boundaries, multi-turn cumulative extraction, and broader harmful-content/protected-material tests require separate fixtures, integration checks, approved safety datasets, or an applicability decision. They were not added as misleading live-data questions.

Scorer fixes from [the review](../../artifacts/evaluation-support/evaluation-question-review.md) are in place: `named_row_cap` v2 counts names-only tables, lists, prose, CSV, and JSON records; `criteria_judge` v3 does not auto-pass a figure that is negated or competes with another plausible value; and the scorecard applies a per-attempt safety gate (safety checks on every row, the behavior verdict on `gate=zero` rows; undecided is `pending`, never passed). `checks` labels describe intended evidence only; the scorecard lists every listed check that produced no result. `--scoring deterministic` (in `run_agent_benchmark.py` and `score_recorded_runs.py`) uses no LLM judge: code-decidable criteria are settled and the rest are `needs_review`.

## Historical Evidence

The exact 84-row dataset used by `full-3` is preserved at [the historical snapshot](../../artifacts/evaluation-support/dataset-snapshots/eval-dataset-full-3-188f3b1ff3a38638.jsonl), SHA-256:

```text
188f3b1ff3a38638aaadfc79c0472828f70e05c108f8d1681c761ce699ce643d
```

Previous attempts, evaluations, reports, and the Excel workbook are unchanged. Do not resume or regrade those runs against revision v2; their stored dataset hash identifies the earlier snapshot. Start a new batch for the revised dataset. No live evaluation was run as part of this update.