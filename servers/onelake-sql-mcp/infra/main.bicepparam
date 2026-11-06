using './main.bicep'

// Identifiers come from the environment so they are never committed, e.g.
//   $env:FABRIC_TENANT_ID = '<tenant-id>'
//   $env:ONELAKE_SQL_ENDPOINT = '<id>.datawarehouse.fabric.microsoft.com'
param location = readEnvironmentVariable('AZURE_LOCATION', 'eastus2')
param fabricTenantId = readEnvironmentVariable('FABRIC_TENANT_ID')
param sqlEndpoint = readEnvironmentVariable('ONELAKE_SQL_ENDPOINT')
param database = readEnvironmentVariable('ONELAKE_DATABASE', 'Wealth_Management')

// Mirrors the semantic model's 15 import partitions (source schemas wm/gold/silver).
param allowedSchemas = ['EXTERNAL_wm', 'EXTERNAL_gold', 'EXTERNAL_silver']
param allowedTables = [
  'EXTERNAL_wm.dim_account'
  'EXTERNAL_wm.dim_date'
  'EXTERNAL_wm.dim_security'
  'EXTERNAL_wm.fact_alert'
  'EXTERNAL_wm.fact_client_interaction'
  'EXTERNAL_wm.fact_opportunity'
  'EXTERNAL_wm.fact_recommendation'
  'EXTERNAL_gold.dim_client'
  'EXTERNAL_gold.dim_advisor_primary'
  'EXTERNAL_gold.dim_advisor_secondary'
  'EXTERNAL_silver.fact_aum_daily'
  'EXTERNAL_silver.fact_holding_snapshot'
  'EXTERNAL_silver.fact_lifecycle_event'
  'EXTERNAL_silver.fact_performance_daily'
  'EXTERNAL_silver.fact_transaction'
]
