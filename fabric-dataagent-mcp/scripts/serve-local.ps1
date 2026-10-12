<#
Run the arm 3 proxy locally as the signed-in Azure CLI user, over HTTP on port 8000.
Reads ..\..\.env (tenant, workspace, data agent, and the host of FABRIC_PROXY_MCP_URL for the Host-header allowlist).
Usage: .\fabric-dataagent-mcp\scripts\serve-local.ps1 [-TunnelHost <host>]
#>
param([string]$TunnelHost)

$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$values = @{}
foreach ($line in Get-Content (Join-Path $root '.env')) {
    if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
}
if (-not $TunnelHost) {
    if (-not $values['FABRIC_PROXY_MCP_URL']) { throw 'Set FABRIC_PROXY_MCP_URL in .env or pass -TunnelHost' }
    $TunnelHost = ([uri]$values['FABRIC_PROXY_MCP_URL']).Host
}

$env:FABRIC_PROXY_TENANT_ID = $values['AZURE_TENANT_ID']
$env:FABRIC_PROXY_WORKSPACE_ID = $values['FABRIC_WORKSPACE_ID']
$env:FABRIC_PROXY_DATA_AGENT_ID = $values['FABRIC_DATA_AGENT_ID']
$env:FABRIC_PROXY_TOKEN_SCOPE = $values['FABRIC_TOKEN_SCOPE']
$env:FABRIC_PROXY_CREDENTIAL_MODE = 'azure_cli'
$env:FABRIC_PROXY_ALLOWED_HOSTS = (@($TunnelHost, 'localhost:*', '127.0.0.1:*') | ConvertTo-Json -Compress)
$env:FABRIC_PROXY_ALLOWED_ORIGINS = (@("https://$TunnelHost", 'http://localhost:*', 'http://127.0.0.1:*') | ConvertTo-Json -Compress)

& (Join-Path $root '.venv\Scripts\python.exe') -m fabric_dataagent_mcp serve --transport http --port 8000
