<#
P15 thin slice: Event Hubs, Azure Managed Redis and the live worker as one Container App.
See docs/architecture/azure.md. Nothing here has been deployed.

  ./infra/deploy.ps1                      build the Bicep locally; no Azure calls (default)
  ./infra/deploy.ps1 -WhatIf -ResourceGroup rg-pitchside
                                          also preview the changes in Azure (read only; az login)
  ./infra/deploy.ps1 -Deploy -ResourceGroup rg-pitchside -Location westeurope
                                          create or update the resources. This costs money.

-Deploy runs main.bicep twice: first without an image (registry, Event Hubs, Redis, environment),
then builds the image in the registry with `az acr build` and deploys again with it.
Publishing a match to the slice: see "Try the slice" in docs/architecture/azure.md.
The script never prints keys or connection strings.
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = '',
    [string]$Location = 'westeurope',
    [string]$Tag = 'p15',
    [switch]$WhatIf,
    [switch]$Deploy
)
$ErrorActionPreference = 'Stop'
$infra = $PSScriptRoot
$root = Split-Path $infra -Parent
$main = Join-Path $infra 'main.bicep'

function Invoke-Az {
    & az @args
    if ($LASTEXITCODE -ne 0) { throw "az $($args[0..2] -join ' ') failed ($LASTEXITCODE)" }
}

Write-Host 'Building the Bicep files locally...'
foreach ($f in Get-ChildItem $infra -Recurse -Filter *.bicep) {
    Invoke-Az bicep build --file $f.FullName --stdout | Out-Null
    Write-Host "  ok  $($f.Name)"
}

if (-not ($WhatIf -or $Deploy)) {
    Write-Host 'Nothing deployed. Use -WhatIf to preview in Azure or -Deploy to deploy.'
    exit 0
}
if (-not $ResourceGroup) { throw 'Give -ResourceGroup for -WhatIf or -Deploy.' }

if ($WhatIf -and -not $Deploy) {
    Invoke-Az deployment group what-if --resource-group $ResourceGroup --template-file $main
    Write-Host 'Preview only; nothing deployed.'
    exit 0
}

Write-Host "Deploying to resource group $ResourceGroup in $Location (this costs money)..."
Invoke-Az group create --name $ResourceGroup --location $Location --output none
$first = Invoke-Az deployment group create --resource-group $ResourceGroup --template-file $main `
    --query properties.outputs --output json | ConvertFrom-Json
$registry = $first.registryName.value
$image = "$($first.loginServer.value)/pitchside-live:$Tag"

Write-Host "Building the worker image in $registry..."
Invoke-Az acr build --registry $registry --image "pitchside-live:$Tag" $root --output none

$second = Invoke-Az deployment group create --resource-group $ResourceGroup --template-file $main `
    --parameters image=$image --query properties.outputs --output json | ConvertFrom-Json
Write-Host "Worker: $($second.workerName.value). Kafka bootstrap: $($second.bootstrap.value)"
Write-Host "Delete everything afterwards: az group delete --name $ResourceGroup"
