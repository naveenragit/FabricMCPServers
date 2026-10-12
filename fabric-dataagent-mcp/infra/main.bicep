// Third web app on the existing wealth-mcp App Service plan and VNet integration subnet.
// It forwards one question to the Fabric data agent MCP endpoint as a service principal.
targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('Prefix for the new web app name.')
param namePrefix string = 'fabric-proxy-mcp'

@description('Existing App Service plan created by semantic-model-mcp/infra/main.bicep.')
param planName string = 'asp-wealth-mcp'

@description('Existing VNet and delegated subnet created by semantic-model-mcp/infra/main.bicep.')
param vnetName string = 'vnet-wealth-mcp'
param appSubnetName string = 'snet-app'

param fabricTenantId string
param workspaceId string
param dataAgentId string

@description('Application (client) ID of the service principal. Managed identities are not supported by the data agent.')
param clientId string

@secure()
@description('Client secret of the service principal; stored as an app setting.')
param clientSecret string

@allowed([
  'https://api.fabric.microsoft.com/.default'
  'https://analysis.windows.net/powerbi/api/.default'
])
param tokenScope string = 'https://api.fabric.microsoft.com/.default'

var siteName = 'app-${namePrefix}-${uniqueString(resourceGroup().id)}'
var siteHost = '${siteName}.azurewebsites.net'

resource plan 'Microsoft.Web/serverfarms@2023-12-01' existing = {
  name: planName
}

resource appSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  name: '${vnetName}/${appSubnetName}'
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: siteName
  location: location
  kind: 'app,linux'
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: appSubnet.id
    vnetRouteAllEnabled: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      vnetRouteAllEnabled: true
      healthCheckPath: '/health'
      appCommandLine: 'python -m fabric_dataagent_mcp serve --transport http'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'FABRIC_PROXY_TENANT_ID', value: fabricTenantId }
        { name: 'FABRIC_PROXY_WORKSPACE_ID', value: workspaceId }
        { name: 'FABRIC_PROXY_DATA_AGENT_ID', value: dataAgentId }
        { name: 'FABRIC_PROXY_TOKEN_SCOPE', value: tokenScope }
        { name: 'FABRIC_PROXY_CREDENTIAL_MODE', value: 'client_secret' }
        { name: 'FABRIC_PROXY_CLIENT_ID', value: clientId }
        { name: 'FABRIC_PROXY_CLIENT_SECRET', value: clientSecret }
        { name: 'FABRIC_PROXY_HOST', value: '0.0.0.0' }
        { name: 'FABRIC_PROXY_PORT', value: '8000' }
        { name: 'FABRIC_PROXY_RESTRICTED_NETWORK_CONFIRMED', value: 'true' }
        { name: 'FABRIC_PROXY_ALLOWED_HOSTS', value: '["${siteHost}"]' }
        { name: 'FABRIC_PROXY_ALLOWED_ORIGINS', value: '["https://${siteHost}"]' }
      ]
    }
  }
}

output siteName string = site.name
output siteHostname string = siteHost
