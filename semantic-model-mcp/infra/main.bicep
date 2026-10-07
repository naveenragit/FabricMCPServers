// VNet-integrated App Service hosting the Wealth Management MCP server, plus the
// Power BI tenant private endpoint used to reach the Fabric semantic model privately.
targetScope = 'resourceGroup'

@description('Region for the virtual network and App Service.')
param location string = resourceGroup().location

@description('Prefix applied to every resource name.')
param namePrefix string = 'wealth-mcp'

@description('Microsoft Entra tenant ID that owns the Fabric tenant.')
param fabricTenantId string

@description('Fabric workspace ID hosting the semantic model.')
param workspaceId string

@description('Semantic model (dataset) ID exposed by the MCP server.')
param semanticModelId string

@description('Report ID bound to the semantic model.')
param reportId string

@description('Address space for the virtual network.')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('Subnet delegated to App Service regional VNet integration.')
param appSubnetPrefix string = '10.20.1.0/24'

@description('Subnet holding the Power BI private endpoint. Needs one address per capacity plus 15.')
param privateEndpointSubnetPrefix string = '10.20.2.0/26'

@description('App Service plan SKU. Basic or higher is required for regional VNet integration.')
param appServicePlanSku string = 'B1'

@description('Create the Power BI private link resources. Requires a Fabric administrator to enable the "Azure Private Link" tenant setting first; otherwise creation is rejected with InvalidRequest.')
param enableFabricPrivateLink bool = false

@description('Expose the operator-only executeDaxQueries effectiveUsername permission probe.')
param enableEffectiveUsernameTest bool = false

@description('Tenant UPNs accepted by the effectiveUsername permission probe. Keep empty when the probe is disabled.')
param effectiveUsernameTestUsers array = []

var suffix = uniqueString(resourceGroup().id)
var vnetName = 'vnet-${namePrefix}'
var appSubnetName = 'snet-app'
var privateEndpointSubnetName = 'snet-privatelink'
var planName = 'asp-${namePrefix}'
var siteName = 'app-${namePrefix}-${suffix}'
var privateLinkServiceName = 'pbi-pl-${namePrefix}'
var privateEndpointName = 'pe-fabric-tenant'
var siteHost = '${siteName}.azurewebsites.net'

// Namespaces covered by the Microsoft.PowerBI 'tenant' private endpoint.
var privateDnsZoneNames = [
  'privatelink.analysis.windows.net'
  'privatelink.pbidedicated.windows.net'
  'privatelink.prod.powerquery.microsoft.com'
]

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: appSubnetName
        properties: {
          addressPrefix: appSubnetPrefix
          delegations: [
            {
              name: 'appservice-delegation'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
        }
      }
      {
        name: privateEndpointSubnetName
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource appSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: appSubnetName
}

resource privateEndpointSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: privateEndpointSubnetName
}

// Target resource for the tenant-scoped Power BI / Fabric private endpoint.
// The type is Microsoft.PowerBI even though the link serves Fabric.
resource privateLinkService 'Microsoft.PowerBI/privateLinkServicesForPowerBI@2020-06-01' = if (enableFabricPrivateLink) {
  name: privateLinkServiceName
  location: 'global'
  properties: {
    tenantId: fabricTenantId
  }
}

// Gated deliberately: api.powerbi.com is a CNAME to api.privatelink.analysis.windows.net,
// so linking an EMPTY privatelink.analysis.windows.net zone to the VNet makes the name
// authoritative and returns NXDOMAIN, breaking Power BI resolution. Create these zones
// only together with the private endpoint that populates them.
resource privateDnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [
  for zone in privateDnsZoneNames: if (enableFabricPrivateLink) {
    name: zone
    location: 'global'
  }
]

resource privateDnsZoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [
  for (zone, index) in privateDnsZoneNames: if (enableFabricPrivateLink) {
    name: '${zone}/link-${vnetName}'
    location: 'global'
    dependsOn: [privateDnsZones[index]]
    properties: {
      registrationEnabled: false
      virtualNetwork: {
        id: vnet.id
      }
    }
  }
]

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (enableFabricPrivateLink) {
  name: privateEndpointName
  location: location
  properties: {
    subnet: {
      id: privateEndpointSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'fabric-tenant-connection'
        properties: {
          privateLinkServiceId: privateLinkService.id
          groupIds: ['tenant']
        }
      }
    ]
  }
}

resource privateEndpointDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (enableFabricPrivateLink) {
  parent: privateEndpoint
  name: 'default'
  dependsOn: [privateDnsZoneLinks]
  properties: {
    privateDnsZoneConfigs: [
      for (zone, index) in privateDnsZoneNames: {
        name: replace(zone, '.', '-')
        properties: {
          privateDnsZoneId: privateDnsZones[index].id
        }
      }
    ]
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: {
    name: appServicePlanSku
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
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
      alwaysOn: appServicePlanSku != 'F1'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      vnetRouteAllEnabled: true
      healthCheckPath: '/health'
      appCommandLine: 'python -m wealth_management_mcp serve --transport http --catalog artifacts/wealth-management-catalog.json'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'WEALTH_MCP_BACKEND', value: 'fabric_rest' }
        { name: 'WEALTH_MCP_TENANT_ID', value: fabricTenantId }
        { name: 'WEALTH_MCP_WORKSPACE_ID', value: workspaceId }
        { name: 'WEALTH_MCP_SEMANTIC_MODEL_ID', value: semanticModelId }
        { name: 'WEALTH_MCP_REPORT_ID', value: reportId }
        { name: 'WEALTH_MCP_CREDENTIAL_MODE', value: 'managed_identity' }
        { name: 'WEALTH_MCP_ALLOW_APPLICATION_IDENTITY', value: 'true' }
        { name: 'WEALTH_MCP_ALLOW_UNVERIFIED_LIVE_DEFINITION', value: 'true' }
        { name: 'WEALTH_MCP_ALLOW_AUTHOR_CATALOG', value: 'true' }
        { name: 'WEALTH_MCP_ENABLE_EFFECTIVE_USERNAME_TEST', value: string(enableEffectiveUsernameTest) }
        { name: 'WEALTH_MCP_EFFECTIVE_USERNAME_ALLOWLIST', value: string(effectiveUsernameTestUsers) }
        { name: 'WEALTH_MCP_HOST', value: '0.0.0.0' }
        { name: 'WEALTH_MCP_PORT', value: '8000' }
        { name: 'WEALTH_MCP_RESTRICTED_NETWORK_CONFIRMED', value: 'true' }
        { name: 'WEALTH_MCP_ALLOWED_HOSTS', value: '["${siteHost}"]' }
        { name: 'WEALTH_MCP_ALLOWED_ORIGINS', value: '["https://${siteHost}"]' }
        { name: 'WEALTH_MCP_CATALOG_PATH', value: 'artifacts/wealth-management-catalog.json' }
      ]
    }
  }
}

output siteName string = site.name
output siteHostname string = siteHost
output principalId string = site.identity.principalId
output vnetName string = vnet.name
output privateEndpointName string = enableFabricPrivateLink ? privateEndpoint.name : ''
