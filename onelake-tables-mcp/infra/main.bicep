// Second web app on the existing wealth-mcp App Service plan and VNet integration subnet,
// serving the lakehouse tables over the SQL analytics endpoint.
targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('Prefix for the new web app name.')
param namePrefix string = 'onelake-mcp'

@description('Existing App Service plan created by semantic-model-mcp/infra/main.bicep.')
param planName string = 'asp-wealth-mcp'

@description('Existing VNet and delegated subnet created by semantic-model-mcp/infra/main.bicep.')
param vnetName string = 'vnet-wealth-mcp'
param appSubnetName string = 'snet-app'

param fabricTenantId string

@description('Lakehouse SQL analytics endpoint host, from the lakehouse sqlEndpointProperties.connectionString.')
param sqlEndpoint string

@description('Lakehouse name; the SQL analytics endpoint database has the same name.')
param database string

@description('Schemas the schema tool reads from.')
param allowedSchemas array

@description('Optional schema.table entries limiting what the schema tool advertises.')
param allowedTables array = []

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
  identity: {
    type: 'SystemAssigned'
  }
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
      appCommandLine: 'python -m onelake_tables_mcp serve --transport http'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'ONELAKE_MCP_TENANT_ID', value: fabricTenantId }
        { name: 'ONELAKE_MCP_SQL_ENDPOINT', value: sqlEndpoint }
        { name: 'ONELAKE_MCP_DATABASE', value: database }
        { name: 'ONELAKE_MCP_ALLOWED_SCHEMAS', value: string(allowedSchemas) }
        { name: 'ONELAKE_MCP_ALLOWED_TABLES', value: string(allowedTables) }
        { name: 'ONELAKE_MCP_CREDENTIAL_MODE', value: 'managed_identity' }
        { name: 'ONELAKE_MCP_HOST', value: '0.0.0.0' }
        { name: 'ONELAKE_MCP_PORT', value: '8000' }
        { name: 'ONELAKE_MCP_RESTRICTED_NETWORK_CONFIRMED', value: 'true' }
        { name: 'ONELAKE_MCP_ALLOWED_HOSTS', value: '["${siteHost}"]' }
        { name: 'ONELAKE_MCP_ALLOWED_ORIGINS', value: '["https://${siteHost}"]' }
      ]
    }
  }
}

output siteName string = site.name
output siteHostname string = siteHost
output principalId string = site.identity.principalId
