# Project Structure and Artifact Cleanup Implementation Plan

- Status: Proposed; revised after the adversarial review and the final review (section 15); awaiting user review and approval.
- Prepared: 2026-11-06.
- Scope: Local repository organization, descriptive names, unused GitHub workflow removal, and artifact cleanup.
- Authorization: Creating this plan does not authorize the moves, renames, deletions, or operational commands below.

## 1. Objectives

- Make the three MCP servers, evaluation harness, datasets, commands, and results easy to distinguish.
- Give scripts names that describe what they do today rather than their historical purpose.
- Separate active inputs, generated outputs, historical evidence, and disposable files.
- Remove unused GitHub automation without replacing it with another workflow.
- Preserve server behavior, evaluation semantics, historical evidence, and the existing Python environment.

## 2. Review Findings

- The repository contains three MCP servers and an evaluation harness, but the root README primarily introduces the semantic-model server.
- `foundry-eval/`, `evals/`, and root evaluation scripts have related names but different responsibilities.
- `scripts/build_eval_dataset.py` validates the canonical dataset without building or rewriting it.
- Benchmark evidence is distributed across run folders, reports, scorecards, metrics, and top-level logs. Batch names such as `full-4` and `e2e-10` do not describe the experiment.
- Current guidance and superseded design documents share the same documentation directory.
- Standard package internals such as `src/`, `tests/`, `infra/`, `deploy/`, and `schemas/` are already appropriate.
- Many generated files are already git-ignored. Cleanup will primarily improve local navigation, not reduce tracked Git history.

## 3. Boundaries and Preservation Rules

- Do not rename the workspace root, recreate `.venv/`, remove `.tools/python/`, or install Python packages globally.
- The existing virtual environment uses Python 3.11.15 from `.tools/python/`; both directories must remain usable.
- Keep Python import packages, distribution names, CLI entry points, MCP tool names, environment-variable names, and cloud resource names unchanged in this reorganization.
- Preserve the source PBIX, semantic overlay, active catalogs, dependency declarations, lockfile, tests, schemas, and license/provenance files.
- Preserve the bytes of datasets, saved run records, and historical reports when moving them. Do not regenerate reference answers or rewrite recorded results.
- Delete nothing produced by the four major runs (`full-1` to `full-4`). Section 8 lists that evidence; it may only be moved, with a hash check before and after.
- Keep raw run records in `artifacts/runs/` and `artifacts/aborted/` exactly where they are. Organize around them, not by moving them.
- Run records, datasets, reference answers, scorecards, metrics, reports, logs, and analysis captures stay git-ignored. None of them is committed or pushed.
- Preserve original batch IDs, run IDs, dataset hashes, and internal JSON/JSONL record filenames. Descriptive directory names are navigation labels, not replacement identifiers.
- Leave existing untracked or user-authored changes untouched unless separately approved. In particular, do not automatically delete `foundry-eval/src/foundry_eval/build_dataset.py`.
- Do not publish private metadata or promote ignored files into tracked source without reviewing their contents first.
- Do not deploy, recreate Foundry agents, change permissions, start cloud evaluations, or delete cloud resources as part of this work.
- Commit locally as `naveenra` after each step passes its checks, so each step can be reverted on its own. Never push. Git cannot restore ignored files, so reverse their moves from the move manifest.

## 4. Proposed Layout

```text
docs/
  project-structure-and-artifact-cleanup-plan.md
  architecture/
  evaluation/
  archive/
servers/
  powerbi-semantic-model-mcp/
  onelake-sql-mcp/
  fabric-data-agent-proxy-mcp/
evaluation/
  harness/
  datasets/
    README.md                            (the only tracked file here)
    wealth-management-benchmark.jsonl    (git-ignored)
scripts/
  model-inspection/
  agent-management/
  evaluation/
  reporting/
  diagnostics/
  access-management/
artifacts/
  runs/                     (unchanged: every run directory stays here)
  aborted/                  (unchanged)
  pbix-inspection/          (unchanged: current inspection)
  wealth-management-catalog*.json   (unchanged: active catalogs)
  model-metadata/
    archive/
  evaluation-support/
    dataset-snapshots/
    reference-answers/
    judge-calibration/
  benchmarks/
    2026-11-05-three-agent-comparison-model-v2/
      experiment.json
      reports/
      scorecard/
      metrics/
      logs/
      analysis/
  concurrency/
  diagnostics/
  archive/
```

- Keep `.env`, `.env.example`, `.venv/`, `.tools/`, `.vscode/`, the root README, and the source PBIX at their existing root locations.
- Keep server-owned deployment scripts inside their respective server projects.
- Keep the concurrency output location initially because the report builder consumes its latest summary.
- Add `scripts/access-management/` to distinguish permission-changing utilities from read-only diagnostics. Relocating such a script does not authorize running it.
- Create archive subdirectories only where they hold actual material; avoid empty organizational scaffolding.
- Treat the experiment directory shown above as an example based on the existing benchmark, not a new experiment to execute.

## 5. Folder and Input Moves

### Server and Evaluation Projects

- Move `semantic-model-mcp/` to `servers/powerbi-semantic-model-mcp/`.
- Move `onelake-tables-mcp/` to `servers/onelake-sql-mcp/`.
- Move `fabric-dataagent-mcp/` to `servers/fabric-data-agent-proxy-mcp/`.
- Move `foundry-eval/` to `evaluation/harness/`.
- Keep their existing Python source package names, including `wealth_management_mcp` and `foundry_eval`.

### Datasets and Supporting Inputs

- First, copy the canonical dataset to `artifacts/evaluation-support/dataset-snapshots/eval-dataset-v7-<hash16>.jsonl` and check the hash. `full-4` recorded the canonical file itself, so today it is the only file its hash check accepts; any later edit would make `full-4` impossible to rescore or report.
- Move `evals/README.md` to `evaluation/datasets/README.md` and update its commands and links.
- Move `evals/eval-dataset.jsonl` to `evaluation/datasets/wealth-management-benchmark.jsonl` without changing its contents. Renaming is safe: run manifests keep the file name for information only, and scoring checks the hash.
- Move historical dataset snapshots from `artifacts/evaluation/` to `artifacts/evaluation-support/dataset-snapshots/`, keeping their version/hash filenames. Keep all of them, including v2 and v6.
- Move the legacy optimizer dataset (`evals/wealth-management-eval.jsonl`) to the same snapshot folder.
- Move reference-answer material (`answer-key.md`, `eval-questions.jsonl`, `dataset-overrides.json`, `adversarial-validation.json`, `ground-truth-verification.sql`) to `artifacts/evaluation-support/reference-answers/`. Never place it under `evaluation/`: it includes Markdown, JSON, and SQL, which a `*.jsonl` ignore rule would not cover.
- Move the judge-calibration baseline responses and calibration outputs to `artifacts/evaluation-support/judge-calibration/`, and update `calibrate_judge.py` in the same change; it reads the baseline and writes its output by fixed path.
- Move the Fabric data-agent instruction source to `artifacts/evaluation-support/`.
- Leave both active catalogs and `artifacts/pbix-inspection/` where they are. The server's default catalog path, the root MCP configuration, the deployment packager's default, `smoke_rest_backend.py`, and the documented export commands all use them, and the deployed package maps the catalog to the same relative path. Moving them adds breakage for no navigation gain.
- Move only the dated backups (`*-2026-09-11.json`, `pbix-inspection-2026-09-11/`, `pbix-inspection-2026-11-05a/`) to `artifacts/model-metadata/archive/`.

## 6. Script Moves and Renames

- Move and rename `scripts/inspect_pbix.py` to `scripts/model-inspection/inspect_pbix.py`.
- Move `scripts/check_model_rls.py` to `scripts/model-inspection/check_model_rls.py`.
- Move the four `foundry_*` project/agent utilities into `scripts/agent-management/`, retaining their filenames initially.
- Move and rename `scripts/build_eval_dataset.py` to `scripts/evaluation/validate_evaluation_dataset.py`.
- Move and rename `scripts/run_eval.py` to `scripts/evaluation/run_agent_benchmark.py`.
- Move and rename `scripts/evaluate_runs.py` to `scripts/evaluation/score_recorded_runs.py`.
- Move `scripts/calibrate_judge.py` to `scripts/evaluation/calibrate_judge.py`.
- Move and rename `scripts/concurrency_test.py` to `scripts/evaluation/benchmark_agent_concurrency.py`.
- Move `scripts/serve_stubs.py` to `scripts/evaluation/serve_stubs.py`; preserve its use for the R12 injection test.
- Move and rename `scripts/scorecard.py` to `scripts/reporting/build_benchmark_scorecard.py`.
- Move and rename `scripts/build_report.py` to `scripts/reporting/build_benchmark_report.py`.
- Move the DAX/identity probes and deployed/backend smoke utilities into `scripts/diagnostics/`.
- Move `scripts/restrict_onelake_identity.py` into `scripts/access-management/`, retaining its filename and behavior.
- Review `artifacts/reports/export_full3_excel.py` individually. It is a hardcoded historical exporter, not a general reporting command; archive it with the original experiment unless generalization is explicitly approved.
- Update help text, examples, subprocess calls, and documentation for every moved command.
- Update repository-root discovery when moving commands one directory deeper. Do not mechanically retain old `parents[...]` or PowerShell parent-directory assumptions.
- Fix script-to-script imports in the same change as the rename. `build_report.py` does `from scorecard import foundry_tool_spans`, and `concurrency_test.py` adds its own folder to `sys.path` and does `import run_eval`. The proposed renames, and splitting these scripts across folders, break both at import. Move the shared functions into `foundry_eval`, or update the imports.
- `smoke_rest_backend.py` adds `semantic-model-mcp/src` to `sys.path`; update it with the server move.
- Use snake_case for Python files and kebab-case for document/data names. Keep conventional entry-point filenames unchanged.

## 7. GitHub Workflow Removal

- Remove `.github/workflows/ci.yml`.
- It is the only workflow. It runs linting, type checks, offline tests, and a wheel build for the semantic-model project; it does not deploy or require Azure credentials.
- Checked 2026-11-06: `main` on GitHub has no branch protection and no rulesets, so no required status check depends on it. It ran three times, all successful, on pushes up to 2026-10-20. Removing it changes nothing for local work or for the deployed servers.
- Remove the workflow entry from the directory-tree example in `docs/implementation-plan.md` and update any references introduced before implementation.
- Remove empty root workflow directories after deleting the file.
- Preserve the semantic-model project's `.github/copilot-instructions.md`; it is editor guidance, not GitHub automation. Update its relative-path guidance if needed after the project moves.
- Document equivalent local verification commands. Do not create a replacement workflow.

## 8. Artifact Cleanup Decisions

### Protected Major-Run Evidence (Never Deleted)

Identified by `batch_id` in each `run.json`, not by file names. Record a SHA-256 for each file before any move, and check it again after.

- Final runs in `artifacts/runs/`: the 10 run directories whose `batch_id` is `full-1` to `full-4` (two each for `full-1` and `full-2`, three each for `full-3` and `full-4`). Every file in them stays, including `steps.jsonl`, `traces.jsonl`, `tool_timings.jsonl`, and the `full-4` Fabric run's `attempts.before-reclassify.jsonl.bak`, which records the infrastructure reclassification.
- Superseded starts in `artifacts/aborted/`: the two first starts of `full-1`, `full-2-dataagent-throttled/`, and `full-4-fabric-old-instructions/`.
- Datasets: the `full-3` snapshot (used by `full-1` to `full-3`) and the canonical dataset plus its new v7 snapshot (used by `full-4`).
- Scorecards `full-1.md` to `full-4.md`; metrics `full-3/`, `full-3.zip`, and `full-4/`; reports `full-3-report.html`, `full-4-report.html`, `full-3-questions-and-responses.xlsx`, `q45-q49-tool-call-breakdown.xlsx`, `evaluation-question-review.md`, and `export_full3_excel.py`.
- Logs: every `full-*`, `proxy-full-*`, `stubs-full-*`, `scorecard-full-*`, and `evaluate-full-*` log, plus `traces-dax.log`, `traces-fabric.log`, `scorecard-run.log`, and `evaluate-fabric-local.log`, all four of which belong to `full-1`, and `calibrate-gpt-6.1-sol.log`.
- Analysis captures: `inspect-full1.txt` to `inspect-full5.txt`, `inspect-forward.txt`, `inspect-traces.txt`, `cloud_status.txt`, and `cloud-failed.txt`. They record investigations of these runs, such as non-completed attempts, judged answers, trace reconciliation, and the `full-1` cloud evaluator status. They move to each experiment's `analysis/` folder.
- Judge-calibration outputs: they record the G8 gate behind the scoring of `full-2` onwards.

### Remove After Approval (Regenerable, Not Run Evidence)

- Tool caches: `.mypy_cache/` (20.84 MiB), `.ruff_cache/`, and pytest and bytecode caches.
- Packaging output: deployment `_stage/` folders, deployment ZIPs, and wheels under `dist/`, when no packaging is running. They are rebuilt by the existing build scripts.

### Archive Instead of Deleting

- Pilot, smoke, end-to-end, and load-test runs remain in `artifacts/runs/`. Move their logs, scorecards, metrics, and reports, along with `artifacts/foundry-smoke/`, to `artifacts/archive/` or a labeled experiment folder.
- Move `scan-result.json`, the RLS scanner output, to `artifacts/diagnostics/`. It is the evidence behind the earlier no-RLS finding.
- Move dated catalog backups and older PBIX inspections to `artifacts/model-metadata/archive/`.
- Keep all dataset snapshots, including v2 and v6. They are small, and deleting them only loses revision history.

### Review Individually

- Review the untracked legacy `foundry_eval/build_dataset.py` with the user. No current import was found, and the dataset guide labels it historical, but it must not be deleted automatically.

### Keep as Active Dependencies or Reproducibility Inputs

- Keep `.tools/python/` and `.venv/`; deleting the former breaks the current environment's base-interpreter reference.
- Keep both current catalogs, current inspection metadata, the semantic overlay, and the source PBIX for server use and model maintenance.
- Keep the canonical dataset and snapshots matching retained saved runs.
- Keep raw run records required for report regeneration, rescoring, comparisons, and trace analysis. Saved runs occupy approximately 128.28 MiB.
- Keep `artifacts/evaluation/semantic-model-responses.jsonl` until its planned move is complete; `scripts/calibrate_judge.py` actively reads it.
- Keep the latest useful concurrency summary; `scripts/build_report.py` reads it when producing the report's concurrency section.
- Keep the Fabric data-agent instruction source for reproducing the published agent configuration.
- Keep dependency declarations, the lockfile, tests, schemas, and third-party license/provenance files.
- Do not blanket-delete proxy logs: `run_eval.py --proxy-log` reads them to populate stored per-call timings. Verify the joins before removing a log.
- Treat exported metrics as derived outputs, not the only copy of evaluation evidence. They occupy approximately 63.74 MiB; retain published/used exports or regenerate them only with the correct source records and dataset revision.

## 9. Benchmark Organization and Compatibility

- Do not move run directories. `RunStore.open(root, run_id)`, `run_eval.py --resume`, `python -m foundry_eval traces` and `server-timings`, `load_tables`, the scorecard, the report builder, and `run_eval.py`'s slowest-first ordering all read `artifacts/runs/<run_id>`. The ordering globs `artifacts/runs/<arm>-*/attempts.jsonl`, so a moved run would silently drop out of it rather than raise an error.
- Give each major batch its own descriptively named folder, such as `artifacts/benchmarks/2026-11-05-three-agent-comparison-model-v2/`. Put its reports, scorecard, metrics, logs, and analysis captures there.
- Use names based on date, purpose, and model revision, not only ordinal labels such as `full-4`.
- Add a small `experiment.json` to each folder with the descriptive name, original batch ID, run IDs, dataset file and hash, and links to `artifacts/runs/<run_id>`. Do not include reference answers, scores, or credentials.
- Use the run manifests' `batch_id` to decide what belongs to each experiment; do not infer membership from file names.
- No harness code changes are needed for this layout. Pass explicit `--out` and `--export` paths when regenerating a scorecard or report.
- Keep a table mapping old batch names to folder names in `artifacts/benchmarks/README.md`, which is ignored. Update local bookmarks, such as the open `full-4` report.

## 10. Documentation and Path Updates

- Rewrite the root README as the project map: server responsibilities, evaluation inputs, run/scoring/reporting commands, and where to find current and historical results.
- Add concise component READMEs where missing; avoid duplicating detailed implementation material.
- Group current architecture/model documentation under `docs/architecture/` and evaluation guidance under `docs/evaluation/`.
- Give the current semantic-model implementation plan and three-agent evaluation plan descriptive filenames within those groups.
- Move the explicitly superseded Foundry comparison plan and historical risk-question design into `docs/archive/`.
- Review mixed current/historical question documentation section by section rather than assuming every old-looking document can be deleted.
- Update relative Markdown links, directory trees, command examples, and paths in the moved documents.
- Update the root and project-local VS Code MCP configurations and associated extension guidance.
- Update PowerShell deployment builders, local proxy launch scripts, CLI defaults, catalog paths, and editable-install references. Known breakages from one more folder level: `semantic-model-mcp/scripts/build-deployment-package.ps1` derives the repository root from two parents of its script folder, so it would look for the catalog under `servers/`. `fabric-dataagent-mcp/scripts/serve-local.ps1` resolves `..\..` for `.env` and `.venv`, so the arm 3 proxy would not start. `semantic-model-mcp/.vscode/mcp.json` and `.env.example` use `../.venv` and `../artifacts`.
- The four editable installs in `.venv` store absolute source paths (`_editable_impl_*.pth`). After each move, reinstall that package editable from the new path with `uv pip install --python .venv\Scripts\python.exe --no-deps --offline -e <new path>`. The build backend, hatchling, is not installed in `.venv` but is in the uv cache, and this command worked after the earlier folder rename. Try it on the first package before moving the rest. If it fails offline, point that package's `.pth` file at the new `src` folder, which is reversible. Do not reinstall dependencies or rebuild `.venv`.
- Move results out of tracked documentation. `docs/foundry-agent-evaluation-plan.md` contains score tables, run IDs, and findings for the pilot, `full-1`, `full-2`, and several end-to-end runs; that text is already on `origin/main`. Move those sections unchanged to an ignored `artifacts/benchmarks/results-history.md`, leaving the method, decisions, and a pointer. Remove any other run results from tracked files in the same way.
- Removing text from current files does not remove it from published GitHub history. Purging published commits requires rewriting history and deleting and recreating the repository; a force-push alone leaves old commits reachable. That is a separate decision and is not part of this plan.
- Review `.env.example` files and local path-valued settings without printing secrets or changing credentials.
- Keep the deployed package's internal source/catalog layout stable; only its local source locations need to change. Do not require cloud redeployment for this cleanup.

## 11. Ignore Rules Before File Moves

- Before moving the dataset, ignore everything under `evaluation/datasets/` except its README (`evaluation/datasets/*` plus `!evaluation/datasets/README.md`). A `*.jsonl`-only rule would leave any other reference file trackable. The existing `evals/*.jsonl` rule does not protect the new location.
- Before every commit, check the staged file list and the staged diff for run IDs, score figures, and reference answers.
- Keep `artifacts/` ignored, including experiment manifests, model metadata, calibration inputs, and generated reports containing private evidence.
- Retain recursive ignores for deployment staging directories and ZIPs after moving projects under `servers/`.
- Preserve ignores for the source PBIX, local secrets, `.tools/`, `.venv/`, caches, and generated build output.
- Keep intentionally shared editor configuration and `.env.example` exceptions intact.
- Check the proposed locations with Git's ignore diagnostics before moving private inputs, and check the staged/tracked file list afterward.
- Do not use broad staging commands that could promote private metadata or unrelated untracked work into the repository.

## 12. Implementation Sequence

Phase 0: preparation (nothing in use changes) - done 2026-11-06

- [x] Obtain approval for the plan and the archive policy.
- [x] Confirm nothing is running: no evaluation run, proxy, stub server, dev tunnel, or the VS Code offline MCP server, and no terminal working inside a folder that will move.
- [x] Record the worktree state, the move manifest, dataset hashes, and a SHA-256 inventory of every protected major-run file (section 8).
- [x] Write a baseline `full-4` scorecard and report to a scratch folder outside the repository.
- [x] Snapshot the v7 dataset and check its hash.

Phase 0 records are in `C:\GitHub\fabric-mcp-restructure-baseline\`, outside the repository:

- `inventory-and-move-manifest.csv`: all 471 ignored files under `artifacts/` and `evals/`, with size, SHA-256, protected flag, and planned Phase A destination; 144 files (201.8 MiB) are protected.
- `tracked-moves.csv`: planned Phase A/B moves of tracked files.
- `environment.json`: HEAD, worktree state, editable-install paths, package locations, and `pyvenv.cfg`.
- `baseline/`: `full-4` scorecard, metrics, and report and `full-3` scorecard, all regenerated by the current code.
- `phase_gate.py`: the gate for every Phase A/B checkpoint. It confirms that every protected file has its original hash, at either its old or its planned path, and that the regenerated outputs match the baseline. It passed on the untouched layout.

What Phase 0 established:

- The v7 snapshot, `artifacts/evaluation-support/dataset-snapshots/eval-dataset-v7-f86393c1aecd2963.jsonl`, has the hash that the `full-4` runs recorded.
- The regenerated `full-4` report is byte-identical to the stored one only when built with its original `--note` (the capacity-throttling limitation), which is saved in the baseline folder. The `full-4` scorecard and all eight metrics files match, apart from the dataset file name line.
- The stored `full-3` scorecard predates the current scorecard sections, so later gates compare against the regenerated baseline, not the stored file.
- Running the scorecard and report builder did not change any protected file: all 144 hashes were checked again afterwards.

Phase A: no command or import paths change - done 2026-11-06

- [x] Remove the workflow; no required checks depend on it. Commit.
- [x] Move results text out of tracked documentation into the ignored results history. Commit.
- [x] Group reports, scorecards, metrics, logs, and analysis captures into experiment folders, leaving run directories in place. Archive non-major material and dated metadata backups.
- [x] Move the evaluation-support files, and update `calibrate_judge.py` paths in the same change.
- [x] Remove the approved caches and packaging output.
- [x] Gate: the protected inventory matches, and the `full-4` scorecard regenerated to scratch matches the baseline. Commit any tracked changes.

What Phase A did:

- Workflow: removed, and `docs/implementation-plan.md` now describes local verification in place of CI.
- Results: the Phase 0 findings, pilot, full runs 1 and 2, end-to-end tests, and G8 figures moved unchanged to the ignored `artifacts/benchmarks/results-history.md`. The tracked plan keeps the method, the decisions, and pointers, and contains no run IDs or score figures. Earlier commits on GitHub still contain the old text.
- Files moved: 180 ignored files, by `phase_a_move.py` in the baseline folder. Each file's hash was checked before and after its move. `moves-log.csv` records every move and can reverse it with `--undo`.
- Layout: each major batch's outputs are in `artifacts/benchmarks/<date>-<batch>-<description>/`, alongside an `experiment.json` listing its final and superseded runs and its dataset snapshot. `artifacts/benchmarks/README.md` maps old batch names to folders. Run directories did not move.
- Evaluation support: dataset snapshots, reference answers, and judge calibration are in `artifacts/evaluation-support/`. `calibrate_judge.py` and four tracked doc links were updated in the same commit.
- Cleanup: 25 cache, staging, and dist folders and 2 deployment ZIPs were removed. All were untracked and are recreated by the tools and build scripts.
- Gate: all 144 protected files match their Phase 0 hashes, and the regenerated `full-4` scorecard, metrics, and report and the `full-3` scorecard and metrics match the baseline.
- Left for Phase B, because these files move or are renamed there: default output paths and help text naming old artifact folders in `run_eval.py` (closing hint), `scorecard.py` (help), `build_report.py` (help and report footer), `check_model_rls.py` (scan output), and `foundry_smoke_agents.py` (docstring). None of them is an input, so nothing breaks. Changing the report footer changes report output, so the gate baseline is then refreshed for that one line.

Phase B: code-touching moves, one small step at a time - done 2026-11-06

- [x] Apply the new ignore rules before moving the dataset.
- [x] Move one server project at a time with `git mv`. Fix its paths, reinstall it editable, check the import location, run its tests, and start it offline. Commit after each server.
- [x] Move the evaluation harness and datasets. Check the import location, run the harness tests and the dataset validator (hash unchanged). Commit.
- [x] Move and rename scripts by group. Fix root depth and script-to-script imports, check each script's computed repository root, and run the touched commands offline. Commit after each group.
- [x] Refresh READMEs and documentation links. Commit.
- [x] Final gate: run the section 13 checks, then one end-to-end question per arm, with your go-ahead. Present the final folder map and commands. A review of every change found two leftover old-folder commands (`uv --project` in the semantic model README) and a mixed-separator usage line, which were fixed. The live check then ran three questions on all three agents, entirely from the new paths. The results are in the git-ignored `artifacts/benchmarks/2026-11-06-e2e-11-restructure-check/`, and the protected-file gate still passed afterwards.

What Phase B did:

- Ignore rules: everything under `evaluation/datasets/` is ignored except its README, checked with `git check-ignore` before the dataset moved. The old `evals/*.jsonl` rule stays as a safeguard.
- Servers: `servers/powerbi-semantic-model-mcp/`, `servers/onelake-sql-mcp/`, and `servers/fabric-data-agent-proxy-mcp/`, one commit each. Each was reinstalled editable with `uv pip install --no-deps --offline -e`, which worked for all four packages, so the `.pth` fallback was not needed. Each passed its tests and Ruff, and each deployment package builds locally with the same layout. The semantic model server reports its offline status. The proxy started from its new folder through `serve-local.ps1` and answered on port 8000. Fixed: the semantic model packager's repository root, `serve-local.ps1`'s root, and the project's `.vscode/mcp.json` and `.env.example`.
- Evaluation: the harness is in `evaluation/harness/`, and the dataset is `evaluation/datasets/wealth-management-benchmark.jsonl` with its hash unchanged. Seven scripts now default to the new dataset path. The untracked `build_dataset.py` moved with its package, unchanged.
- Scripts: 20 scripts moved into six groups. The repository root moved from `parents[1]` to `parents[2]`, and the two sibling imports now use the new module names. Output paths now point to `artifacts/diagnostics/` (RLS scan, raw smoke events) and `artifacts/benchmarks/` (help text, closing hint, report footer). `--help` was run on all 10 argparse scripts after checking that each parses before doing any work. Ruff found one regression, a missing `Path` import, which was fixed; the 4 other findings predate Phase B.
- Report footer: only the footer text changed in the `full-4` report, so that baseline file was refreshed, and the old copy is kept as `baseline/full-4-report.before-footer-change.html`.
- Docs: the seven design and evaluation documents are in `docs/architecture/`, `docs/evaluation/`, and `docs/archive/`. Every relative link was recomputed and none is broken. The root README is now a project map with common commands, and it holds no run results.
- Final offline check (`final_check.py` in the baseline folder): all four test suites, Ruff, mypy, the dataset hash, script roots and `--help`, the gate (144 protected files plus the regenerated `full-4` and `full-3` outputs), Markdown links, offline server status, editable-install paths, no stale folder paths, nothing private tracked, and no workflow. All pass.

## 13. Verification and Acceptance Criteria

- Check the relevant existing tests after each code/path change; run package test suites separately because test module names can collide across projects.
- Run lint on touched Python files and the existing semantic-model type check where applicable. Do not perform unrelated formatting cleanup.
- Validate the canonical dataset with the renamed validator; its row count and SHA-256 must match the baseline.
- Check that every protected major-run file is still present with the same SHA-256 and that the file count is unchanged.
- Check that the `full-3` scorecard also regenerates to the scratch folder, that its snapshot passes the dataset hash check, and that its totals match the stored scorecard.
- Check that `git ls-files` lists nothing under `artifacts/` and only the README under `evaluation/datasets/`, and that `git check-ignore` covers the dataset and reference-answer files.
- Perform offline MCP startup/handshake checks using the existing root environment; do not make a live data query solely for reassurance.
- Build relevant deployment packages locally and inspect their source/catalog layout without uploading them.
- Regenerate the `full-4` scorecard and report offline into a scratch folder outside the repository, never onto the stored files. Use the v7 snapshot and the existing concurrency summary. Compare them with the Phase 0 baseline: run count, attempt count, correctness totals, safety outcomes, timing and token summaries, and the concurrency section. A run that merely completes proves nothing, because `load_tables` silently skips run IDs it cannot find, and the report silently drops its concurrency section if that folder is missing.
- Do not run `evaluate_runs.py`, `run_eval.py --resume`, `python -m foundry_eval traces` or `server-timings`, or `calibrate_judge.py` against protected runs during verification. They append to run files or overwrite calibration output.
- After moving each script, check that its computed repository root contains `.env` and `.git`. Scripts that read `.env` first fail loudly on a wrong root, but the scorecard and report builder would read an empty or wrong folder without failing.
- After each package move, check that `<package>.__file__` points into the new folder.
- Confirm moved historical datasets, raw records, and reports are byte-identical to their originals.
- Check current documentation links and executable path references. Historical command text retained as evidence should be labeled, not blindly rewritten.
- Confirm private datasets/artifacts remain ignored and no unrelated user changes were removed or staged.
- Confirm no workflow remains accidentally referenced as a required local step, and no new automation has been introduced.
- Finish with one end-to-end question per arm through the real agents. It creates a new small run under a new batch name, leaving the protected runs untouched, and needs the proxy, the tunnel, and both App Services. It is the only check that exercises the full path that works today, and it calls Foundry and Fabric, so it needs your go-ahead. No full evaluation, cloud deployment, or test matrix is needed.

## 14. Recovery and Handover

- Use small, reversible move batches and keep the move manifest until verification is complete.
- If a batch fails, repair its path references or reverse only the moves from that batch. Do not use destructive Git resets or restore unrelated user files.

## 15. Adversarial Review (2026-11-06)

The review checked the first draft of this plan against the code, the run manifests, the Git index and history, and the GitHub settings. Sections 3 to 13 above include the fixes.

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | The draft deleted `inspect-full*.txt`, `cloud_status.txt`, `cloud-failed.txt`, `full-3.zip`, and possibly major-run console logs. They are products of the four major runs. | A protected inventory in section 8; these files are only moved. |
| 2 | High | Major-run evidence is also in `artifacts/aborted/`, and the `full-4` Fabric run holds a pre-reclassification backup. The draft left both unprotected. | Both are in the protected inventory. |
| 3 | High | `full-4` used the canonical dataset with no frozen copy, so any later edit would block rescoring and reporting it. | Snapshot v7 first. |
| 4 | High | Moving runs into benchmark folders breaks the run store, resume, trace joins, the scorecard, and the report builder. It also silently drops runs from the slowest-first ordering. | Runs stay in `artifacts/runs/`; benchmark folders hold everything else and point to the runs. |
| 5 | High | Answer keys, overrides, and validation SQL were to go to `evaluation/datasets/archive/`, but the planned ignore rule covered only `*.jsonl`, so they could have been committed. | Reference material stays under `artifacts/`; `evaluation/datasets/` ignores everything except the README. |
| 6 | High | Run results are already on GitHub: the evaluation plan on `origin/main` holds score tables and run IDs for the pilot, `full-1`, `full-2`, and end-to-end tests. Raw run records, datasets, and logs were never tracked or committed. The 16 unpushed commits add no results, only report-builder code. | Move results text to an ignored file. Purging published history is a separate decision. |
| 7 | Medium | Editable installs record absolute paths. Every package import breaks after the server and harness moves. | Reinstall each package editable after its move. |
| 8 | Medium | Parent-folder assumptions in the deployment packager, the proxy launcher, and the project MCP configuration break with one more folder level. | Named fixes in section 10. |
| 9 | Medium | Renaming `scorecard.py` and `run_eval.py` breaks `build_report.py` and `concurrency_test.py` at import. | Move the shared functions into `foundry_eval` or update the imports in the same change. |
| 10 | Medium | Moving the active catalogs breaks the server default, the MCP configuration, the packager default, a smoke test, and documented commands. | The catalogs stay; only dated backups move. |
| 11 | Low | Removing the workflow was blocked on an unverified required check. | Verified: no protection or rulesets on `main`. |
| 12 | Low | The draft offered v2 and v6 dataset snapshots and the RLS scan output for deletion. | Keep the snapshots; move the scan output to diagnostics. |
| 13 | Low | Moving files while a run, proxy, or tunnel is active could corrupt append-only records. | Added a precondition. |

Verified safe and unchanged: renaming the dataset file (scoring checks the hash, not the name), removing caches and packaging output, keeping `.tools/` and `.venv/`, and keeping the deployed package layout.

### Final Review (2026-11-06)

| # | Severity | Finding | Fix |
|---|---|---|---|
| 14 | High | Verification could overwrite protected evidence. Regenerating the `full-4` report or scorecard to its usual path replaces the stored file, and `evaluate_runs.py`, `--resume`, trace joins, and judge calibration append to run files or overwrite their output. | Regenerate only into a scratch folder; those commands are not run during verification. |
| 15 | High | Some breakage fails silently. `load_tables` skips missing run IDs, the report drops its concurrency section if the folder is missing, and each script finds the repository from its own location. | Compare counts with a baseline instead of trusting a clean exit, and check the repository root of each moved script. |
| 16 | Medium | One large change leaves no rollback point, and Git cannot restore ignored files. | Phases with gates, a local commit after each step, and a move manifest for ignored files. |
| 17 | Medium | Editable reinstalls need hatchling offline, and it is not in `.venv`. | It is in the uv cache. Try it on one package first; repointing the `.pth` file is the fallback. |
| 18 | Medium | Offline checks do not prove that the live path still works. | One end-to-end question per arm as the final gate, with your approval. |
| 19 | Low | On Windows, a process working inside a folder blocks moving that folder. | Stop the VS Code MCP server and services first, and use `git mv` one folder at a time. None were running at review time. |
| 20 | Low | Some text names old paths: the schema-drift test message, Bicep descriptions, and `run_eval.py`'s closing hint. | Update them with the move; no behavior depends on them. |

Confirmed unaffected: the deployed App Services and Foundry agents use URLs, not repository paths. `.env` contains no repository paths. Generated reports and scorecards contain no relative links, and no script launches another script by path.
- Preserve the baseline report and retained raw evidence until the final comparison passes.
- Present the final folder map, updated commands, cleanup/deletion summary, and any remaining compatibility locations.
- Report checks that could not be completed, including unresolved remote GitHub required-check settings.
- Implementation remains pending user approval; this document records a proposal, not completed work.