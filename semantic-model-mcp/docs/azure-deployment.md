# Azure deployment: VNet-integrated App Service + Fabric private link

Subscription `<subscription-id>` (`<subscription-name>`),
tenant `<tenant-id>`, region `eastus2`,
resource group `rg-wealth-mcp`.

## Corrected Fabric identifiers

The IDs embedded in `sm_wealth_mgmt_import.pbix` belong to a different tenant and return
`Unauthorized` here. The same model exists in this tenant under different IDs, which were
resolved from the Power BI REST API and are what the deployment uses:

| Object | PBIX-embedded (not valid here) | Live in this tenant |
| --- | --- | --- |
| Workspace | `<pbix-workspace-id>` | `<workspace-id>` (`WealthManagementDemo`) |
| Semantic model | `<pbix-model-id>` | `<semantic-model-id>` |
| Report | `<pbix-report-id>` | `<report-id>` |

The workspace runs on capacity `<capacity-id>`
(`<capacity-name>`, **F8**, East US 2). This matters because private link is
**not supported on Trial capacity**, and the tenant also holds an `FTL64` trial capacity.

Regenerate the catalog against these IDs with:

```powershell
python -m wealth_management_mcp export `
  --metadata artifacts/pbix-inspection/model-metadata.json `
  --summary artifacts/pbix-inspection/inspection-summary.json `
  --overlay semantic-model-mcp/semantic-models/sm_wealth_mgmt_import/semantic-overlay.yaml `
  --output artifacts/wealth-management-catalog-mcaps.json `
  --model-id <semantic-model-id> `
  --workspace-id <workspace-id> `
  --report-id <report-id>
```

## What is deployed

`infra/main.bicep` (resource-group scope) creates:

| Resource | Name | Notes |
| --- | --- | --- |
| Virtual network | `vnet-wealth-mcp` | `10.20.0.0/16` |
| Subnet | `snet-app` | `10.20.1.0/24`, delegated to `Microsoft.Web/serverFarms` |
| Subnet | `snet-privatelink` | `10.20.2.0/26`, private endpoint policies disabled |
| Private DNS zones | `privatelink.analysis.windows.net`, `privatelink.pbidedicated.windows.net`, `privatelink.prod.powerquery.microsoft.com` | gated; see the DNS warning below |
| App Service plan | `asp-wealth-mcp` | Linux, B1 |
| Web app | `<app-name>` | Python 3.11, system-assigned identity, regional VNet integration, `vnetRouteAllEnabled` |
| Private endpoint | `pe-fabric-tenant` | gated behind `enableFabricPrivateLink` |
| Private link service | `pbi-pl-wealth-mcp` | `Microsoft.PowerBI/privateLinkServicesForPowerBI`, gated |

## Verified end state

- `GET /health` → `{"status":"alive","mode":"fabric_rest"}`
- MCP Streamable HTTP handshake succeeds (protocol `2026-07-28`)
- `tools/list` → `execute_dax_query`, `get_report_metadata`, `get_semantic_model_schema`
- `resources/list` → `wealth://status`, `wealth://schemas/fabric-dax/1.0.0`
- Tool calls reach Power BI and return
  `Backend identity was not authorized for this semantic model` — the expected result until
  the two tenant settings below are enabled.

The same tools were verified returning **real data** locally under `AzureCliCredential`
(for example `EVALUATE ROW("Tables", COUNTROWS(INFO.VIEW.TABLES()))` → `15`, matching the
offline catalog), so only identity authorization is outstanding.

> `aiohttp` is a required dependency. `azure-identity`'s async `ManagedIdentityCredential`
> builds an aiohttp pipeline, and omitting it fails only on App Service — `AzureCliCredential`
> shells out to `az` and never exercises that transport, so local runs hide the problem.

Subnet sizing follows the documented rule of one address per capacity plus 15; the tenant
has three capacities, so `/26` is ample.

## Optional v0.2 effectiveUsername test

The v0.2 package can expose `execute_dax_query_as_user` to verify whether an impersonated
semantic-model user needs Build in addition to Read. The tool is disabled by default and
accepts only UPNs in `WEALTH_MCP_EFFECTIVE_USERNAME_ALLOWLIST`.

Before enabling it, restrict inbound access to the App Service, temporarily promote its
managed identity to workspace Admin, and prepare two tenant test users with exact semantic
model permissions `Read` and `ReadExplore` respectively. Neither user should inherit a
higher workspace role. See [the package README](../README.md#effective-username-permission-probe-v02)
for configuration, the constant-result MCP probe, expected outcomes, and cleanup.

Deploy:

```powershell
az deployment group create `
  --resource-group rg-wealth-mcp `
  --subscription <subscription-id> `
  --parameters infra/main.bicepparam
```

Package and publish the app:

```powershell
./scripts/build-deployment-package.ps1
az webapp deploy --resource-group rg-wealth-mcp `
  --name <app-name> --type zip `
  --src-path deploy/wealth-management-mcp.zip
```

## Outstanding prerequisites (tenant administrator only)

Two blockers are **not** fixable from Azure and were left for a Fabric administrator.
`enableFabricPrivateLink` therefore defaults to `false`.

### 1. Azure Private Link tenant setting

Creating `Microsoft.PowerBI/privateLinkServicesForPowerBI` failed with:

> `InvalidRequest` — Private link service creation or update is forbidden. Operation can only be performed by tenant administrator.

Fix: Fabric portal → **Tenant settings** → **Azure Private Link** → *Enabled*. Propagation
takes roughly 15 minutes and publishes a tenant-specific FQDN. Then redeploy with
`enableFabricPrivateLink = true`.

Do **not** enable **Block Public Internet Access** unless the whole tenant is ready for it.
It is tenant-wide and breaks email subscriptions, Copilot, on-premises data gateways, and
semantic-model-to-semantic-model connections.

### 2. Authorizing the managed identity

Two separate things are required, and they fail in different places.

**a. Workspace role — done.** Use the **Fabric** `roleAssignments` API, not the legacy
Power BI `groups/{id}/users` API. The legacy endpoint cannot resolve a managed identity and
fails with `InvalidRequest — Failed to get service principal details from AAD`, which looks
like a tenant-setting problem but is not. The Fabric API takes the **object ID** and accepts
`ServicePrincipal` directly:

```powershell
az rest --method post `
  --url "https://api.fabric.microsoft.com/v1/workspaces/<workspace-id>/roleAssignments" `
  --resource "https://analysis.windows.net/powerbi/api" `
  --headers "Content-Type=application/json" `
  --body '{"principal":{"id":"<identity-object-id>","type":"ServicePrincipal"},"role":"Member"}'
```

Verify with `GET .../roleAssignments`; the identity should appear as
`<app-name> / ServicePrincipal / Member`.

**b. Tenant setting — outstanding.** The Fabric admin portal setting is
**Tenant settings → Developer settings → "Service principals can use Fabric APIs"**, scoped
to *The entire organization* or a security group containing the managed identity. It covers
managed identities as well as app registrations.

> Older docs — including the Execute Queries page — still call this *"Allow service
> principals to use Power BI APIs"*. That name no longer appears in the portal.

The separate **Dataset Execute Queries REST API** setting is already enabled here, proven by
a user token succeeding against the same endpoint.

Confirmed by `GET /diagnostics/identity` on the deployed app:

```json
{
  "token_acquired": true,
  "token_claims": {"aud": "https://analysis.windows.net/powerbi/api",
                   "tid": "<tenant-id>", "oid": "<identity-object-id>",
                   "appid": "<identity-app-id>", "idtyp": "app"},
  "power_bi": {"http_status": 401, "error": ""},
  "fabric":   {"http_status": 401,
               "error": "{\"errorCode\":\"Unauthorized\",\"message\":\"The caller is not authenticated to access this resource\"}"}
}
```

Every part of the token is correct — audience, tenant, and an `oid` matching the identity
that holds the workspace role — and `idtyp: app` confirms an app-only token. Both Power BI
**and** Fabric reject it as *not authenticated*, which is an authentication-layer refusal of
app-only tokens rather than a permissions decision.

Read the status codes as follows:

| Observation | Meaning |
| --- | --- |
| `token_acquired: true` | Entra issued a Power BI token; identity and scope are correct |
| `401` on both endpoints | Token refused before item permissions were evaluated — tenant setting |
| `403` | Token accepted; the identity lacks access to the workspace or item |

Because the workspace role is already correct, a permissions problem would appear as `403`.

**The scoping trap.** The tenant setting can be scoped to *The entire organization* or to
specific security groups. This managed identity belongs to **no security group at all**, so
a group-scoped setting excludes it and looks identical to the setting being off:

```powershell
az rest --method get `
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/<identity-object-id>/transitiveMemberOf"
# -> empty
```

**c. Execute Queries is gated separately.** With the above resolved, metadata reads return
`200` while DAX execution still returns `401 PowerBINotAuthorizedException`:

```json
"power_bi": {"http_status": 200},
"fabric":   {"http_status": 200},
"execute_queries": {"http_status": 401,
                    "error": "{\"code\":\"PowerBINotAuthorizedException\"}"}
```

That combination is conclusive: the identity authenticates and can read the workspace and
report, so only DAX execution is blocked. The control is the
**Semantic Model Execute Queries REST API** tenant setting — formerly *Dataset Execute
Queries REST API*, and listed under **Integration settings** in the current portal. It is
already on for users here (a user token executes DAX successfully), so check its
**security-group scope** and ensure it covers the managed identity, or set it to the entire
organization.

Tenant settings encountered in this project, with their current portal names:

| Current name | Former name | Governs |
| --- | --- | --- |
| Service principals can call Fabric public APIs | Allow service principals to use Power BI APIs | whether app-only tokens are accepted at all |
| Semantic Model Execute Queries REST API | Dataset Execute Queries REST API | DAX execution over REST |
| Users can use the Power BI Model Context Protocol server endpoint (preview) | — | the preview endpoint that returns 403 for `remote_mcp` |

Allow roughly 15 minutes for tenant setting changes to take effect.

Identity reference: `<app-name>`,
object ID `<identity-object-id>`,
application ID `<identity-app-id>`.

## What private link does and does not cover

The Power BI tenant private endpoint (`tenant` subresource) covers exactly three
namespaces: `analysis.windows.net`, `pbidedicated.windows.net` and
`prod.powerquery.microsoft.com`.

`api.powerbi.com` **is** covered, which was confirmed by resolving its CNAME chain:

```
api.powerbi.com
  -> api.privatelink.analysis.windows.net
    -> <guid>.trafficmanager.net
      -> wabi-us-east-a-primary-redirect.analysis.windows.net
        -> wabi-us-east-a-primary-comp-ev2.eastus.cloudapp.azure.com  (A)
```

Because the public name deliberately routes through `api.privatelink.analysis.windows.net`,
creating the private endpoint puts the REST data path — including Execute Queries — onto
the private link. No application change is needed.

### Do not create the private DNS zones before the private endpoint

This chain has a sharp edge that was hit during deployment. Linking an **empty**
`privatelink.analysis.windows.net` zone to the VNet makes that zone authoritative for
`api.privatelink.analysis.windows.net`. With no A record present, resolution returns
NXDOMAIN instead of following the public CNAME, so `api.powerbi.com` becomes unresolvable
and every Power BI call fails from inside the VNet:

```
"host": "api.powerbi.com", "addresses": [], "routing": "unresolved",
"error": "Name or service not known"
```

`main.bicep` therefore gates the zones, the VNet links, and the private endpoint behind the
single `enableFabricPrivateLink` flag. Never create the zones on their own.

### Other constraints

- **Workspace-level** private links cannot be used here at all: Power BI semantic models
  are explicitly unsupported in workspaces with workspace-level private links. Tenant-level
  is the only option for this scenario.
- Private link is a network control. It does not fix authorization, so it would not have
  resolved the `403` from the preview MCP endpoint.

Verify routing from inside the VNet with the built-in endpoint, which reports how each
hostname resolves and flags private versus public addresses:

```
GET https://<app-name>.azurewebsites.net/diagnostics/network
```

## Data path

The preview Microsoft endpoint `https://api.fabric.microsoft.com/v1/mcp/powerbi` returns
`403` / MCP error `-32003` in this tenant, so the server ships a `fabric_rest` backend that
uses the Power BI **Execute Queries** REST API instead. That path is verified working
against the live model.

Because Microsoft's wire tool names are unpublished and undiscoverable here, the REST mode
exposes **project-owned** tools rather than impersonating Microsoft identifiers:

| Tool | Role (per Microsoft docs) |
| --- | --- |
| `execute_dax_query` | Execute Query |
| `get_semantic_model_schema` | Get Semantic Model Schema: compact view by default (~9K tokens: tables, columns, measures with exact DAX names, relationships); `detail="full"` returns the complete OSSIE-derived profile (~80K tokens) |
| `get_report_metadata` | Get Report Metadata (re-verifies report-to-model binding) |

Guards retained in REST mode: model/report allowlisting, `EVALUATE`-only queries, row and
response-byte ceilings, request timeouts, and origin pinning to `api.powerbi.com`.

## Security notes for this POC

- There is **no inbound authentication** on the MCP endpoint, and it is reachable on a
  public App Service hostname. Restrict it before using real data — App Service access
  restrictions, Easy Auth, or a private endpoint on the web app itself.
- All callers share one backend identity, so **row-level security is not enforced per
  caller**. The model defines an advisor RLS rule
  (`[advisor_email] == USERPRINCIPALNAME()`), which a service principal does not satisfy.
