# Wealth Management MCP

Python **3.11** implementation of the [project plan](../docs/implementation-plan.md), inside the existing project. The Python environment, PBIX, and private generated artifacts stay in the parent project. No nested environment, additional Fabric workspace, cloud deployment, or model-security change is created.

## Implemented and verified

- Deterministic **OSSIE-derived Fabric/DAX Profile 1.0.0** generation, bundled upstream schema with verified provenance, decoded extension schemas and semantic validation.
- Actual PBIX catalog: **15 datasets, 171 fields, 53 first-class DAX metrics, 33 relationships (28 active / 5 inactive)**. DAX definitions, types, native bindings and inactive state are preserved.
- [Formula-derived draft descriptions](semantic-models/sm_wealth_mgmt_import/semantic-overlay.yaml) for all tables/measures. Business-owner verification is still needed; guidance never changes DAX.
- MCP resource preview over **stdio and loopback Streamable HTTP**, with no client authentication.
- Remote backend discovery and a reviewed-contract registration mechanism for Microsoft's four documented roles. Exact names/inputs are never guessed.
- Fixed backend credential provider, model/report allowlists, report-binding checks, positive native success/error policies, bounded HTTP/result handling, explicit disclosure/definition approval gates, and safe diagnostics.
- Passing pytest, Ruff, and mypy checks, real stdio/TCP HTTP smoke tests, and a distributable wheel with schema resources verified outside the source directory.

## Live status

The server is deployed to a VNet-integrated App Service and **executes DAX against the live
semantic model** using its system-assigned managed identity. See
[docs/azure-deployment.md](docs/azure-deployment.md) for the infrastructure.

Verified end to end through the deployed MCP endpoint:

```
tools: ['execute_dax_query', 'get_report_metadata', 'get_semantic_model_schema']
execute_dax_query -> {"rows": [{"[Tables]": 15}]}          # matches the offline catalog
execute_dax_query -> {"[Measure]": "Total Clients", "[Table]": "dim_client"}, ...
```

Microsoft's **preview** MCP endpoint (`api.fabric.microsoft.com/v1/mcp/powerbi`) still returns
**HTTP 403 / MCP error -32003**. The cause is now known: the tenant setting
`PowerBIMCP` — *"Users can use the Power BI Model Context Protocol server endpoint (preview)"* —
is disabled. The `remote_mcp` backend therefore remains unused, and there is still no reviewed
Microsoft tool manifest, so `remote_mcp` advertises zero tools rather than inventing identifiers.

The `fabric_rest` backend used instead exposes **project-owned** tools implementing Microsoft's
documented roles. The names are ours and do not impersonate Microsoft's unpublished wire names.

Resource URIs:

- `wealth://models/sm_wealth_mgmt_import/schema` — private offline author catalog, not a live or caller-specific schema.
- `wealth://schemas/fabric-dax/1.0.0` — derivative structural schema. Full validation also applies extension and semantic rules.
- `wealth://status` — configuration/status without a live readiness assertion.

Use the resources explicitly in an MCP host; not all agents automatically attach resources.

## Choosing a DAX endpoint

Power BI exposes two DAX endpoints. They authorize differently, and the difference decides
whether a service principal works at all.

| | `executeQueries` (legacy) | `executeDaxQueries` |
| --- | --- | --- |
| Response | JSON | **Apache Arrow IPC**, LZ4-compressed |
| Capacity | Pro, PPU, Premium/Fabric | **Premium or Fabric only** |
| Managed identity | **401 `PowerBINotAuthorizedException`** | **200 — works** |
| Delegated Azure CLI user token | 200 | 401 (token lacks the delegated scope) |
| INFO functions | not supported | supported |
| Errors | HTTP 4xx | **HTTP 200 with an error rowset** |

Select with `WEALTH_MCP_DAX_ENDPOINT`; the default is `execute_dax_queries` because the
deployed app authenticates as a managed identity. Use `execute_queries` for local development
under `AzureCliCredential`, where the inverse holds.

Two consequences worth knowing:

- `executeDaxQueries` reports **query failures as HTTP 200**, carrying an error rowset flagged
  by `IsError` in the Arrow schema metadata. Status codes alone are not a success signal, so
  [rest_backend.py](src/wealth_management_mcp/rest_backend.py) inspects that metadata and
  raises rather than returning an empty result.
- Decoding requires **pyarrow**, which is a large install. On a B1 plan the Oryx build takes
  noticeably longer than the dependency set that preceded it.

## Effective username permission probe (v0.2)

Version 0.2 adds an operator-only `execute_dax_query_as_user` tool for testing the
`executeDaxQueries` `effectiveUsername` contract. It is hidden unless explicitly enabled,
and submitted UPNs must match a server-side allowlist. The supplied UPN is a test identity;
it is not derived from or authenticated as the MCP caller, so this is not per-caller isolation.

The experiment needs:

1. Restrict inbound access to the MCP app. It has no application-level client authentication.
2. Two distinct tenant test users with no Admin, Member, or Contributor role inherited from
  the workspace: one with semantic-model `Read`, and one with `Read + Build`. In REST
  permission responses, Build is named `Explore`, so the expected values are `Read` and
  `ReadExplore`.
3. Temporarily change the App Service managed identity from workspace Member to workspace
  Admin. Microsoft permits `effectiveUsername` only when the calling identity is a workspace
   Admin. Capture its assignment ID, promote it, and retain that ID for rollback:

   ```powershell
   $workspaceId = '<workspace-id>'
   $managedIdentityObjectId = '<managed-identity-object-id>'
   $assignments = az rest --method get `
     --url "https://api.fabric.microsoft.com/v1/workspaces/$workspaceId/roleAssignments" `
     --resource 'https://analysis.windows.net/powerbi/api' | ConvertFrom-Json
   $assignmentId = ($assignments.value | Where-Object {
     $_.principal.id -eq $managedIdentityObjectId
   }).id
   if (-not $assignmentId) { throw 'Managed identity workspace assignment was not found' }
   az rest --method patch `
     --url "https://api.fabric.microsoft.com/v1/workspaces/$workspaceId/roleAssignments/$assignmentId" `
     --resource 'https://analysis.windows.net/powerbi/api' `
     --headers 'Content-Type=application/json' --body '{"role":"Admin"}'
   ```

4. Use dedicated test users with no pre-existing direct model access and no access inherited
  from workspace roles or security groups. Grant exact item-level permissions. These calls
  require a user/operator with `ReadReshare` on the semantic model; item permissions cannot
  reduce a higher permission inherited from a workspace or group:

   ```powershell
   $modelId = '<semantic-model-id>'
   $datasetUsersUrl = "https://api.powerbi.com/v1.0/myorg/groups/$workspaceId/datasets/$modelId/users"
   $readOnlyUser = 'read-only-user@contoso.com'
   $buildUser = 'build-user@contoso.com'
   $readBody = @{identifier=$readOnlyUser; principalType='User'; datasetUserAccessRight='Read'} |
     ConvertTo-Json -Compress
   $buildBody = @{identifier=$buildUser; principalType='User'; datasetUserAccessRight='ReadExplore'} |
     ConvertTo-Json -Compress
   az rest --method post --url $datasetUsersUrl `
     --resource 'https://analysis.windows.net/powerbi/api' `
     --headers 'Content-Type=application/json' --body $readBody
   az rest --method post --url $datasetUsersUrl `
     --resource 'https://analysis.windows.net/powerbi/api' `
     --headers 'Content-Type=application/json' --body $buildBody
   az rest --method get --url $datasetUsersUrl `
     --resource 'https://analysis.windows.net/powerbi/api' `
     --query "value[?identifier=='$readOnlyUser' || identifier=='$buildUser'].{user:identifier,access:datasetUserAccessRight}"
   ```

5. Deploy v0.2 and set the two feature settings. They can be supplied through the Bicep
  parameters `enableEffectiveUsernameTest` and `effectiveUsernameTestUsers`, or directly as
  App Service settings:

  ```powershell
  $allowlist = '["read-only-user@contoso.com","build-user@contoso.com"]'
  az webapp config appsettings set `
    --resource-group <resource-group> `
    --name <app-name> `
    --settings WEALTH_MCP_ENABLE_EFFECTIVE_USERNAME_TEST=true `
           "WEALTH_MCP_EFFECTIVE_USERNAME_ALLOWLIST=$allowlist"
  ```

6. Run the constant-result probe through MCP:

  ```powershell
  $env:WEALTH_MCP_URL = 'https://<app-name>.azurewebsites.net/mcp'
  $env:WEALTH_MCP_SEMANTIC_MODEL_ID = '<semantic-model-id>'
  $env:WEALTH_MCP_READ_ONLY_TEST_USER = 'read-only-user@contoso.com'
  $env:WEALTH_MCP_BUILD_TEST_USER = 'build-user@contoso.com'
  .\.venv\Scripts\python.exe scripts\probe_effective_username_mcp.py
  ```

The probe sends only `EVALUATE ROW("Probe", 1)` and expects this matrix:

| Identity context | Expected |
| --- | --- |
| Backend service principal, no effective user | Success |
| Read-only effective user | Failure |
| Read+Build effective user | Success |

This proves the permission gate without retrieving business rows. The currently deployed
model has no RLS roles, so it cannot prove different row visibility. A separate model copy
with test RLS and a nonconstant query is required for that second test.

After collecting the result, disable `WEALTH_MCP_ENABLE_EFFECTIVE_USERNAME_TEST`, clear the
allowlist, return the managed identity to Member, and remove the temporary users' item-level
permissions:

```powershell
az rest --method patch `
  --url "https://api.fabric.microsoft.com/v1/workspaces/$workspaceId/roleAssignments/$assignmentId" `
  --resource 'https://analysis.windows.net/powerbi/api' `
  --headers 'Content-Type=application/json' --body '{"role":"Member"}'
$noneReadBody = @{identifier=$readOnlyUser; principalType='User'; datasetUserAccessRight='None'} |
  ConvertTo-Json -Compress
$noneBuildBody = @{identifier=$buildUser; principalType='User'; datasetUserAccessRight='None'} |
  ConvertTo-Json -Compress
az rest --method put --url $datasetUsersUrl `
  --resource 'https://analysis.windows.net/powerbi/api' `
  --headers 'Content-Type=application/json' --body $noneReadBody
az rest --method put --url $datasetUsersUrl `
  --resource 'https://analysis.windows.net/powerbi/api' `
  --headers 'Content-Type=application/json' --body $noneBuildBody
```

## Service principal access to a semantic model

All of the following were required. Each fails differently, and the failures are easy to
misread as one another.

**1. Workspace role — use the Fabric API, not the Power BI one.**
`POST api.powerbi.com/v1.0/myorg/groups/{id}/users` cannot resolve a managed identity and
fails with `InvalidRequest — Failed to get service principal details from AAD`, which looks
like a tenant-setting problem but is not. Use the Fabric endpoint, which takes the **object
ID** (not the application ID):

```powershell
az rest --method post `
  --url "https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/roleAssignments" `
  --resource "https://analysis.windows.net/powerbi/api" `
  --headers "Content-Type=application/json" `
  --body '{"principal":{"id":"<objectId>","type":"ServicePrincipal"},"role":"Contributor"}'
```

**2. Semantic model permission.** The principal needs read **and build**. Confirm with
`GET /groups/{ws}/datasets/{ds}/users`; this deployment shows
`datasetUserAccessRight: ReadWriteExplore`, where *Explore* carries Build.

**3. Tenant settings.** Names have been renamed repeatedly and current docs still cite retired
ones, so verify against the API rather than the documentation:

```powershell
az rest --method get --url "https://api.fabric.microsoft.com/v1/admin/tenantsettings" `
  --resource "https://analysis.windows.net/powerbi/api"
```

| `settingName` | Current portal title | Required for |
| --- | --- | --- |
| `ServicePrincipalAccessPermissionAPIs` | Service principals can call Fabric public APIs | any app-only token being accepted |
| `DatasetExecuteQueries` | Semantic Model Execute Queries REST API | DAX over REST |
| `OnPremAnalyzeInExcel` | Allow XMLA endpoints and Analyze in Excel… | `executeDaxQueries` |
| `PowerBIMCP` | Users can use the Power BI MCP server endpoint (preview) | the native `remote_mcp` backend |

Scope matters as much as the toggle: each can apply to the entire organization or to security
groups. A managed identity belongs to **no security group by default**, so a group-scoped
setting silently excludes it and looks exactly like the setting being off.

**4. Hard blocks no setting overrides.** Service principals cannot use these endpoints against
a model with **RLS** or with **SSO** enabled. Neither applies here — confirmed as `0` RLS roles
and `PbixInImportMode` via [scripts/check_model_rls.py](../scripts/check_model_rls.py), which
uses the admin scanner because `INFO.ROLES()` is blocked through Execute Queries.

### Reading the failure modes

`GET /diagnostics/identity` on the deployed app probes each layer and reports token claims plus
per-endpoint status, which separates causes that otherwise look identical:

| Symptom | Cause |
| --- | --- |
| `token_acquired: false` | identity/credential misconfiguration |
| `401` on **all** endpoints | app-only tokens refused — `ServicePrincipalAccessPermissionAPIs` |
| `401` on `executeQueries` only, metadata `200` | legacy endpoint rejects the managed identity — use `executeDaxQueries` |
| `403` | token accepted; missing workspace role or Build permission |
| `200` with an `IsError` rowset | DAX or model error, not authorization |

The distinction that took longest to pin down: **401 means the token was refused before
permissions were evaluated, 403 means permissions were evaluated and denied.** A correct
workspace role with a 401 therefore points at the endpoint or tenant settings, never at
permissions.

## Layout

- [pyproject.toml](pyproject.toml) / [uv.lock](uv.lock) — package and exact dependency resolution.
- [src/wealth_management_mcp/profile.py](src/wealth_management_mcp/profile.py) — catalog generation/semantic validation.
- [src/wealth_management_mcp/schemas/PROVENANCE.md](src/wealth_management_mcp/schemas/PROVENANCE.md) — pinned OSSIE provenance and derivative rules.
- [src/wealth_management_mcp/server.py](src/wealth_management_mcp/server.py) — MCP resources and reviewed tool dispatch.
- [src/wealth_management_mcp/contracts.py](src/wealth_management_mcp/contracts.py) — explicit role/signature/response mappings.
- [src/wealth_management_mcp/backend.py](src/wealth_management_mcp/backend.py) — authenticated Microsoft MCP transport and report binding.
- [src/wealth_management_mcp/cli.py](src/wealth_management_mcp/cli.py) — export, validate, discovery, status and serve commands.
- [tests/test_profile.py](tests/test_profile.py), [tests/test_server.py](tests/test_server.py), [tests/test_contracts.py](tests/test_contracts.py), [tests/test_protocol_smoke.py](tests/test_protocol_smoke.py) — synthetic tests, never live business rows.

## Environment and installation

Run the following from the **parent project root** in PowerShell. Reuse its existing Python 3.11 x64 environment; do not create a nested environment or use the unrelated Python environment on PATH.

```powershell
.\.venv\Scripts\python.exe -c "import sys; assert sys.prefix != sys.base_prefix and sys.version_info[:2] == (3,11)"
$env:UV_PROJECT_ENVIRONMENT = (Join-Path $PWD '.venv')
uv sync --project semantic-model-mcp --python .venv/Scripts/python.exe --locked --group dev --group inspection --inexact --system-certs
```

The lock currently references the available Microsoft package mirror. **SDK 2.1.1** is pinned: the mirror did not contain the planned 2.2.0, and direct public wheel downloads failed TLS negotiation. No certificate verification was disabled. A move to another index/version requires a deliberate re-lock and test run. The `inspection` group is optional and unnecessary for a runtime-only installation. `--inexact` preserves unrelated existing parent-environment inspection packages.

## Generate and inspect the actual catalog

```powershell
.\.venv\Scripts\python.exe -m wealth_management_mcp export --metadata artifacts/pbix-inspection/model-metadata.json --summary artifacts/pbix-inspection/inspection-summary.json --overlay semantic-model-mcp/semantic-models/sm_wealth_mgmt_import/semantic-overlay.yaml --output artifacts/wealth-management-catalog.json
.\.venv\Scripts\python.exe -m wealth_management_mcp validate artifacts/wealth-management-catalog.json
.\.venv\Scripts\python.exe -m wealth_management_mcp status --catalog artifacts/wealth-management-catalog.json
```

This uses extracted metadata, not table rows or a DAX engine. A catalog hash proves internal integrity/determinism, not live-definition equality or imported-data freshness. Raw role expressions/memberships/M/source credential containers are excluded, but free-text descriptions still require review before disclosure.

## Start the local preview

The current parent workspace has [../.vscode/mcp.json](../.vscode/mcp.json). Start **wealth-management-mcp** in VS Code's MCP configuration; it launches the package in stdio mode. The configuration in [.vscode/mcp.json](.vscode/mcp.json) is for opening this child folder as the workspace instead. Both use the existing parent environment and catalog. Debug with VS Code's Python debugger or an MCP client; the SDK's high-level `mcp dev` command is not used for this explicit low-level server.

For HTTP:

```powershell
.\.venv\Scripts\python.exe -m wealth_management_mcp serve --catalog artifacts/wealth-management-catalog.json --transport http --port 8000
```

The endpoint is `http://127.0.0.1:8000/mcp`; `/health` means process liveness only. Stop with Ctrl+C. This POC uses JSON request/response and does not implement long-lived GET subscriptions. No public HTTP listener is running merely because the package is installed.

## Native `remote_mcp` integration, after the preview 403 is resolved

This path is **not** what the deployment uses; `fabric_rest` is. It applies only if you want
Microsoft's native MCP endpoint, which requires the `PowerBIMCP` tenant setting above.

1. Confirm the Fabric tenant and existing model/report binding. Azure subscription access does not establish Fabric permissions.
2. Verify the remote MCP tenant setting, required backend access/consent, and a supported fixed-identity sign-in. Do not grant broad permissions or change tenant settings blindly to resolve 403.
3. Discover actual contracts (no query execution):

	```powershell
	.\.venv\Scripts\python.exe -m wealth_management_mcp discover --tenant-id <confirmed-tenant-id> --output artifacts/power-bi-discovery.json
	```

4. Review the generated manifest. It intentionally starts with `status: unreviewed` and an empty `roles` list. Map only the four documented capabilities to actual names and explicit object-key paths. Set `response_policy_reviewed`, positive schema-success path/value, native error paths and query row paths based on observed provider responses. Paths with `*` apply only to reviewed response arrays. Complex input `$ref`/composition mappings require a dedicated adapter, not a guessed path.
5. Keep the discovered tool hash intact and set `status: reviewed` only after review. The manifest is an operator-controlled local artifact, not a security signature and never a client-uploaded file. Synthetic names under tests are **not** discovery evidence.
6. Configure `WEALTH_MCP_BACKEND=remote_mcp`, `WEALTH_MCP_TENANT_ID`, and `WEALTH_MCP_CONTRACT_PATH`. Explicitly approve `WEALTH_MCP_ALLOW_AUTHOR_CATALOG=true` only for the fixed POC audience. To knowingly run despite an unverified live-definition fingerprint, set `WEALTH_MCP_ALLOW_UNVERIFIED_LIVE_DEFINITION=true`; this does not certify freshness.
7. Generate Query stays disabled unless `WEALTH_MCP_ENABLE_GENERATE_QUERY=true` and actual Microsoft eligibility/context semantics are verified. Report calls perform an independent Get Report REST binding check, which has separate permissions. The `fabric_rest` backend is a deliberate, separately configured data path rather than a silent fallback from `remote_mcp`.

Credential modes: `azure_cli` for an explicitly signed-in operator; `managed_identity` for hosted compute, which additionally requires `WEALTH_MCP_ALLOW_APPLICATION_IDENTITY=true`. No implicit credential chain or user-to-user OBO. Managed identity is now **live-verified** against `executeDaxQueries`; note it is rejected by the legacy `executeQueries`. Local CLI tokens are not automatically available on hosted compute. Never paste tokens/secrets into chat, configuration committed to source control, or tool inputs.

## Deployment boundary

The server is deployed to App Service with regional VNet integration; see
[docs/azure-deployment.md](docs/azure-deployment.md). A non-loopback bind requires `WEALTH_MCP_RESTRICTED_NETWORK_CONFIRMED=true` and explicit `WEALTH_MCP_ALLOWED_HOSTS` / `WEALTH_MCP_ALLOWED_ORIGINS` JSON-array environment values. That acknowledgment **does not create a network restriction**, and the deployed endpoint is currently reachable on a public App Service hostname **with no inbound authentication**. All callers share the backend identity, so no per-caller RLS is claimed. Restrict ingress before using real data.

Defaults: 60-second overall request deadline, 1,000 total rows at reviewed response paths, 8 MiB upstream/HTTP output limit. HTTP limits apply after final SDK framing; stdio checks reserve framing space conservatively. Unsupported compression/result formats and native error/partial-result envelopes are rejected rather than returned as successful complete data. Successful live responses still carry unknown completeness/freshness provenance unless separately evidenced. Local timeout does not prove the remote engine stopped.

## Verify and package

```powershell
.\.venv\Scripts\python.exe -m ruff check semantic-model-mcp/src semantic-model-mcp/tests
.\.venv\Scripts\python.exe -m mypy --config-file semantic-model-mcp/pyproject.toml --follow-imports silent semantic-model-mcp/src
.\.venv\Scripts\python.exe -m pytest semantic-model-mcp/tests -q
uv build --project semantic-model-mcp --python .venv/Scripts/python.exe --wheel --out-dir semantic-model-mcp/dist --system-certs
```

Vendored OSSIE retains its Apache license/notice. This project-specific derivative is **not unmodified OSSIE, Apache-endorsed DAX support, a complete Fabric round-trip exporter, or verified Microsoft wire compatibility**.