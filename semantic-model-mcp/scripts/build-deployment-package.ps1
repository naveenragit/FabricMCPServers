# Stage and zip the App Service payload: the package source, its runtime
# requirements, and the catalog bound to the live Fabric identifiers.
[CmdletBinding()]
param(
    [string]$Catalog = 'artifacts/wealth-management-catalog-mcaps.json'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$projectRoot = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $projectRoot 'deploy/_stage'
$zip = Join-Path $projectRoot 'deploy/wealth-management-mcp.zip'

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
if (Test-Path $zip) { Remove-Item $zip -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

# Package source, excluding caches so the uploaded tree matches the repository.
Copy-Item -Path (Join-Path $projectRoot 'src/wealth_management_mcp') -Destination $stage -Recurse
Get-ChildItem -Path $stage -Include '__pycache__' -Recurse -Directory |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Copy-Item -Path (Join-Path $projectRoot 'deploy/requirements.txt') -Destination $stage

$catalogSource = Join-Path $repoRoot $Catalog
if (-not (Test-Path $catalogSource)) { throw "Catalog not found: $catalogSource" }
New-Item -ItemType Directory -Path (Join-Path $stage 'artifacts') -Force | Out-Null
Copy-Item -Path $catalogSource -Destination (Join-Path $stage 'artifacts/wealth-management-catalog.json')

Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
Write-Output "Package: $zip"
Get-ChildItem $stage | Select-Object Name, Mode
