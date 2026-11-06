using './main.bicep'

// Identifiers and the secret come from the environment so they are never committed, e.g.
//   $env:FABRIC_TENANT_ID = '<tenant-id>'
//   $env:FABRIC_WORKSPACE_ID = '<workspace-id>'
//   $env:FABRIC_DATA_AGENT_ID = '<data-agent-id>'
//   $env:FABRIC_SPN_CLIENT_ID = '<app-id>'
//   $env:FABRIC_SPN_CLIENT_SECRET = '<secret>'
param location = readEnvironmentVariable('AZURE_LOCATION', 'eastus2')
param fabricTenantId = readEnvironmentVariable('FABRIC_TENANT_ID')
param workspaceId = readEnvironmentVariable('FABRIC_WORKSPACE_ID')
param dataAgentId = readEnvironmentVariable('FABRIC_DATA_AGENT_ID')
param clientId = readEnvironmentVariable('FABRIC_SPN_CLIENT_ID')
param clientSecret = readEnvironmentVariable('FABRIC_SPN_CLIENT_SECRET')
param tokenScope = readEnvironmentVariable('FABRIC_TOKEN_SCOPE', 'https://api.fabric.microsoft.com/.default')
