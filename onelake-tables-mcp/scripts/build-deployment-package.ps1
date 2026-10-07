# Stage and zip the App Service payload: package source plus runtime requirements.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $projectRoot 'deploy/_stage'
$zip = Join-Path $projectRoot 'deploy/onelake-tables-mcp.zip'

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
if (Test-Path $zip) { Remove-Item $zip -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

Copy-Item -Path (Join-Path $projectRoot 'src/onelake_tables_mcp') -Destination $stage -Recurse
Get-ChildItem -Path $stage -Include '__pycache__' -Recurse -Directory |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -Path (Join-Path $projectRoot 'deploy/requirements.txt') -Destination $stage

Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
Write-Output "Package: $zip"
