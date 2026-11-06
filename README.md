# Fabric Semantic Model MCP

Read-only MCP servers over one wealth-management dataset in Microsoft Fabric, and the harness that evaluates three Foundry agents built on them: one querying the semantic model with DAX, one querying the lakehouse tables with SQL, and one asking a Fabric data agent.

> **Identifiers in this repository are placeholders.** Tenant, subscription, workspace,
> semantic model, report, capacity, and managed identity IDs have been replaced with
> non-functional values such as `<workspace-id>`, and the App Service
> name with `<app-name>`. Substitute your own before deploying; see
> [servers/powerbi-semantic-model-mcp/docs/azure-deployment.md](servers/powerbi-semantic-model-mcp/docs/azure-deployment.md).
>
> The source `.pbix` is deliberately **not** published; it stays git-ignored. Regenerate the
> catalog from your own model with `scripts/model-inspection/inspect_pbix.py` and the `export` command.

## Project map

```text
servers/                              MCP servers, one installable Python package each
  powerbi-semantic-model-mcp/         semantic model schema + DAX (package wealth_management_mcp)
  onelake-sql-mcp/                    lakehouse schema + read-only T-SQL (package onelake_tables_mcp)
  fabric-data-agent-proxy-mcp/        pass-through proxy to a Fabric data agent (package fabric_dataagent_mcp)
evaluation/
  harness/                            run records, recorder, evaluators, Foundry agents (package foundry_eval)
  datasets/                           README tracked; the benchmark dataset itself is git-ignored
scripts/
  model-inspection/                   PBIX metadata inspection, RLS scan
  agent-management/                   create, show, probe, and smoke-test the Foundry agents
  evaluation/                         run a benchmark, score runs, validate the dataset, R12 stub, load test
  reporting/                          scorecard and HTML report from stored runs
  diagnostics/                        smoke tests and probes against deployed servers
  access-management/                  changes database permissions; run only deliberately
docs/
  architecture/                       semantic model MCP design, model assessment, design review
  evaluation/                         three-agent evaluation plan, question set
  archive/                            superseded designs, kept for history
artifacts/                            git-ignored: catalogs, runs, benchmarks, reports, reference answers
```

| Component | Start here |
|---|---|
| Semantic model MCP server | [servers/powerbi-semantic-model-mcp/README.md](servers/powerbi-semantic-model-mcp/README.md) |
| Evaluation design and method | [docs/evaluation/three-agent-evaluation-plan.md](docs/evaluation/three-agent-evaluation-plan.md) |
| Evaluation dataset | [evaluation/datasets/README.md](evaluation/datasets/README.md) |
| Question set and traps | [docs/evaluation/evaluation-questions.md](docs/evaluation/evaluation-questions.md) |
| Semantic model MCP design | [docs/architecture/semantic-model-mcp-implementation-plan.md](docs/architecture/semantic-model-mcp-implementation-plan.md) |
| Findings from the PBIX | [docs/architecture/semantic-model-assessment.md](docs/architecture/semantic-model-assessment.md) |
| This layout and its cleanup | [docs/project-structure-and-artifact-cleanup-plan.md](docs/project-structure-and-artifact-cleanup-plan.md) |

Inside `artifacts/` (git-ignored), each major evaluation batch has a folder under `artifacts/benchmarks/` holding its logs, scorecard, metrics, reports, and an `experiment.json` listing its runs; `artifacts/benchmarks/README.md` maps batch names to folders. Raw run records stay in `artifacts/runs/<run_id>/`, where the harness reads them. Dataset snapshots, reference answers, and judge calibration are under `artifacts/evaluation-support/`.

## Common commands

Run from the repository root with the repository virtual environment.

```powershell
# Tests, one package at a time (test module names collide across packages)
.\.venv\Scripts\python.exe -m pytest servers/powerbi-semantic-model-mcp/tests -q
.\.venv\Scripts\python.exe -m pytest servers/onelake-sql-mcp/tests -q
.\.venv\Scripts\python.exe -m pytest evaluation/harness/tests -q
Push-Location servers/fabric-data-agent-proxy-mcp; ..\..\.venv\Scripts\python.exe -m pytest tests -q; Pop-Location

# Semantic model MCP server, offline over stdio
.\.venv\Scripts\python.exe -m wealth_management_mcp serve --catalog artifacts/wealth-management-catalog.json --transport stdio

# Evaluation: validate the dataset, run the three agents, score, then report
.\.venv\Scripts\python.exe scripts\evaluation\validate_evaluation_dataset.py
.\.venv\Scripts\python.exe scripts\evaluation\run_agent_benchmark.py --arms dax sql fabric --repeats 3 --batch <batch>
.\.venv\Scripts\python.exe scripts\evaluation\score_recorded_runs.py <run_id> [<run_id> ...]
.\.venv\Scripts\python.exe scripts\reporting\build_benchmark_scorecard.py <run_ids> --out artifacts\benchmarks\<folder>\scorecard\<batch>.md --export artifacts\benchmarks\<folder>\metrics
.\.venv\Scripts\python.exe scripts\reporting\build_benchmark_report.py <dax_run> <sql_run> <fabric_run> --name <batch> --out artifacts\benchmarks\<folder>\reports\<batch>-report.html
```

The Fabric data agent arm needs the proxy running locally and reachable through a dev tunnel (`servers\fabric-data-agent-proxy-mcp\scripts\serve-local.ps1`, then host the tunnel). The R12 prompt-injection row also needs `scripts\evaluation\serve_stubs.py`. The other two servers run on Azure App Service; their packaging scripts are in each server's `scripts/` folder.

## Status

- The semantic model server's project-owned `fabric_rest` backend is deployed and runs live DAX through `executeDaxQueries`. The catalog holds **15 tables, 171 columns, 54 measures, 37 relationships** (model update 2026-11-05). Microsoft's preview Power BI MCP endpoint stays blocked by a disabled tenant setting, so the `remote_mcp` mode exposes no invented Microsoft tools.
- The lakehouse server is deployed on the same App Service plan. The data agent proxy runs locally as the signed-in user, because the data agent does not accept managed identities.
- **Scope:** no inbound MCP authentication (accepted for this POC); all clients share each server's backend identity, and no per-caller row-level isolation is claimed. Do not use real client data.
- The Python package design (official MCP Python SDK v2, Pydantic, ASGI/Uvicorn, pytest/Ruff/mypy on Python 3.11) and the OSSIE-derived Fabric/DAX semantic profile are described in the [design document](docs/architecture/semantic-model-mcp-implementation-plan.md). The semantic profile is intentionally adapted; it is not unmodified OSSIE or a drop-in replacement for Microsoft's endpoint.
- Evaluation results are kept out of the repository, under `artifacts/benchmarks/`.

## Local inspection environment

The repository-local virtual environment uses **Python 3.11 x64**, because PBIXRay's native dependencies do not publish Windows ARM64 wheels in the versions inspected. Its base runtime is workspace-local, installed without global executable or registry registration. No global Python packages or other project environments were changed.

The implementation reuses this root environment with locked project dependencies. It pins official MCP SDK **2.1.1** because the configured mirror lacked 2.2.0 and direct wheel downloads failed TLS; certificate verification was not weakened. The environment was not rebuilt and the PBIX remains unchanged.

The inspection utility accepts a source PBIX path and optional output directory. Its default outputs are private, git-ignored metadata under the artifacts directory. It does not query Power BI, decode business table rows, refresh, or modify the PBIX. Reuse the explicit repository-local virtual-environment interpreter when rerunning it.

The source PBIX, extracted artifacts, virtual environment, downloaded runtime, and secret configuration are excluded by [.gitignore](.gitignore). Review descriptions and metadata before sharing this repository; even structural metadata can reveal business context.

## Licence

Released under the [MIT License](LICENSE).

The vendored Apache OSSIE schema under `servers/powerbi-semantic-model-mcp/src/wealth_management_mcp/schemas/` keeps its own Apache-2.0 [LICENSE](servers/powerbi-semantic-model-mcp/src/wealth_management_mcp/schemas/LICENSE) and [NOTICE](servers/powerbi-semantic-model-mcp/src/wealth_management_mcp/schemas/NOTICE); see [PROVENANCE.md](servers/powerbi-semantic-model-mcp/src/wealth_management_mcp/schemas/PROVENANCE.md). This project is a derivative work and is **not** unmodified OSSIE, nor endorsed by the Apache Software Foundation.
