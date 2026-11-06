using './main.bicep'

// Fabric identifiers are read from the environment so they are never committed.
// Set these before deploying, for example:
//   $env:FABRIC_TENANT_ID = '<tenant-id>'
param location = readEnvironmentVariable('AZURE_LOCATION', 'eastus2')
param namePrefix = readEnvironmentVariable('APP_NAME_PREFIX', 'wealth-mcp')
param fabricTenantId = readEnvironmentVariable('FABRIC_TENANT_ID')
param workspaceId = readEnvironmentVariable('FABRIC_WORKSPACE_ID')
param semanticModelId = readEnvironmentVariable('FABRIC_SEMANTIC_MODEL_ID')
param reportId = readEnvironmentVariable('FABRIC_REPORT_ID')
param appServicePlanSku = readEnvironmentVariable('APP_SERVICE_PLAN_SKU', 'B1')
// Flip to true only after a Fabric administrator enables the "Azure Private Link"
// tenant setting; Azure rejects the resource until then.
param enableFabricPrivateLink = false
