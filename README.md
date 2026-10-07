# Wealth Management Semantic Model MCP

Development workspace for a read-only MCP façade over a Power BI semantic model with Apache OSSIE-aligned metadata.

> **Identifiers in this repository are placeholders.** Tenant, subscription, workspace,
> semantic model, report, capacity, and managed identity IDs have been replaced with
> non-functional values such as `<workspace-id>`, and the App Service
> name with `<app-name>`. Substitute your own before deploying — see
> [semantic-model-mcp/docs/azure-deployment.md](semantic-model-mcp/docs/azure-deployment.md).
>
> The source `.pbix` is deliberately **not** published; it stays git-ignored. Regenerate the
> catalog from your own model with `scripts/inspect_pbix.py` and the `export` command.

## Deliverables

- **[semantic-model-mcp/README.md](semantic-model-mcp/README.md)** — implemented Python package, local preview, verification results and live-integration steps.
- **[docs/implementation-plan.md](docs/implementation-plan.md)** — architecture, OSSIE/DAX compatibility, MCP tool contracts, naming, folder structure, authentication, phased backlog, and acceptance criteria.
- **[docs/foundry-evaluation-plan.md](docs/foundry-evaluation-plan.md)** — three-arm Foundry agent evaluation through MCP: raw data, semantic model without measures, and semantic model with measures; selected quality metrics and token/tool-effort measurements.
- **[docs/model-assessment.md](docs/model-assessment.md)** — findings from the actual PBIX, including relationship, RLS, measure, report, and AI-metadata observations.
- **[docs/adversarial-review.md](docs/adversarial-review.md)** — source-backed review findings, corrections, evidence checks, and unresolved implementation gates.
- [scripts/inspect_pbix.py](scripts/inspect_pbix.py) — reproducible offline metadata-only inspection utility.
- [scripts/probe_effective_username_mcp.py](scripts/probe_effective_username_mcp.py) — constant-result MCP probe for the `effectiveUsername` Read versus Build permission matrix.
- [requirements-inspection.txt](requirements-inspection.txt) — pinned inspection dependency; not proposed server runtime dependencies.

## Current status

The Python package is implemented under **[semantic-model-mcp](semantic-model-mcp/README.md)**. Offline generation/validation and MCP stdio/HTTP resources work; pytest, Ruff, and mypy pass, and the wheel's bundled schema resources were verified outside the source folder. The actual catalog preserves **15 tables, 171 columns, 53 measures, 33 relationships**.

The project-owned `fabric_rest` backend is deployed and has executed live DAX through the Arrow-returning `executeDaxQueries` endpoint. Microsoft's native preview MCP endpoint remains blocked by its disabled tenant setting, so the `remote_mcp` mode still exposes zero invented Microsoft tools. Version **0.2.0** adds a disabled-by-default, allowlisted operator probe for testing the `effectiveUsername` Read versus Build requirement; it does not add per-caller identity propagation.

**Confirmed scope:** no MCP client authentication for this POC; host on restricted/private Azure compute in the user's MCAPS tenant and reuse the PBIX workspace/model/report IDs against the associated Fabric environment. All reachable clients share one configured backend identity and approved POC data scope. Fabric/Power BI still requires backend credentials; no per-caller advisor isolation is claimed.

Recommended design: **Trusted POC client → Python 3.11 MCP façade on MCAPS compute → authenticated Power BI backend → published Fabric semantic model**. Microsoft's remote MCP adapter is the preferred capability provider if backend access/contracts are proved; it is not mandated by the documentation.

**Standards boundary:** the public surface follows Microsoft's **Execute Query, Get Semantic Model Schema, Get Report Metadata, and Generate Query** roles. Exact wire names/input schemas require discovery; no custom metric tools replace them. Semantic definitions use an explicitly versioned **OSSIE-derived Fabric/DAX Profile 1.0.0**, retaining first-class DAX metrics and all relationships with active/inactive state. This intentionally adapted schema is not unmodified OSSIE or a proven drop-in replacement for Microsoft's endpoint.

The plan uses the official MCP Python SDK v2, Pydantic models, ASGI/Uvicorn HTTP hosting, and pytest/Ruff/mypy in one installable Python package. Python 3.11 is required for the server, metadata utilities, and tests; no .NET application or runtime is required. Local Desktop DAX execution is deferred rather than introducing a managed-runtime bridge.

POC checks cover backend credentials from compute, reused model/report binding, fixed metadata exposure and restricted ingress. Inbound OAuth/JWT/OBO and two-user RLS/OLS tests are **deferred**, not initial implementation blockers. Existing model security is not removed; REST service-principal execution is not an automatic option for this RLS-containing model. Completeness and imported-data freshness remain explicit unknowns where evidence is missing.

The plan records the current Azure CLI default **`<subscription-name>`** as a deployment candidate. A second user MCAPS environment is also available, so the exact subscription/compute must be confirmed before deployment. Azure CLI and VS Code Azure extension authentication are separate; the extension was signed out while CLI account metadata was available. No account selection or cloud resources were changed.

## Local inspection environment

The repository-local virtual environment uses **Python 3.11 x64**, because PBIXRay's native dependencies do not publish Windows ARM64 wheels in the versions inspected. Its base runtime is workspace-local, installed without global executable or registry registration. No global Python packages or other project environments were changed.

The implementation reuses this root environment with locked project dependencies. It pins official MCP SDK **2.1.1** because the configured mirror lacked 2.2.0 and direct wheel downloads failed TLS; certificate verification was not weakened. The environment was not rebuilt and the PBIX remains unchanged.

The inspection utility accepts a source PBIX path and optional output directory. Its default outputs are private, git-ignored metadata under the artifacts directory. It does not query Power BI, decode business table rows, refresh, or modify the PBIX. Reuse the explicit repository-local virtual-environment interpreter when rerunning it.

The source PBIX, extracted artifacts, virtual environment, downloaded runtime, and secret configuration are excluded by [.gitignore](.gitignore). Review descriptions and metadata before sharing this repository; even structural metadata can reveal business context.

## Licence

Released under the [MIT License](LICENSE).

The vendored Apache OSSIE schema under `semantic-model-mcp/src/wealth_management_mcp/schemas/` keeps its own Apache-2.0 [LICENSE](semantic-model-mcp/src/wealth_management_mcp/schemas/LICENSE) and [NOTICE](semantic-model-mcp/src/wealth_management_mcp/schemas/NOTICE); see [PROVENANCE.md](semantic-model-mcp/src/wealth_management_mcp/schemas/PROVENANCE.md). This project is a derivative work and is **not** unmodified OSSIE, nor endorsed by the Apache Software Foundation.
