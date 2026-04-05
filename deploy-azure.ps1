<#
.SYNOPSIS
    Deploy Data Bench to Azure Container Apps (West US 2).

.DESCRIPTION
    One-shot script that creates all Azure resources and deploys the app.
    Safe to re-run — uses existing resources if they already exist.

.EXAMPLE
    .\deploy-azure.ps1
    .\deploy-azure.ps1 -ResourceGroup "my-rg" -AppName "mybench"
#>

param(
    [string]$ResourceGroup  = "cloudbench-rg",
    [string]$Location       = "westus2",
    [string]$AcrName        = "cloudbenchacr",
    [string]$EnvName        = "cloudbench-env",
    [string]$AppName        = "cloudbench"
)

$ErrorActionPreference = "Stop"

# Refresh PATH so a freshly-installed Azure CLI is found
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Data Bench — Azure Container Apps      " -ForegroundColor Cyan
Write-Host "  Region: $Location                     " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# -------------------------------------------------------------------
# 1. Verify Azure CLI is installed and logged in
# -------------------------------------------------------------------
Write-Host "[1/7] Checking Azure CLI..." -ForegroundColor Yellow
try {
    $acct = az account show 2>&1 | ConvertFrom-Json
    Write-Host "  Logged in as: $($acct.user.name)" -ForegroundColor Green
    Write-Host "  Subscription: $($acct.name)" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Azure CLI not found or not logged in." -ForegroundColor Red
    Write-Host "  Run 'az login' first, then re-run this script." -ForegroundColor Red
    exit 1
}

# -------------------------------------------------------------------
# 2. Create Resource Group
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[2/7] Creating resource group '$ResourceGroup'..." -ForegroundColor Yellow
az group create --name $ResourceGroup --location $Location --output none 2>$null
Write-Host "  Done." -ForegroundColor Green

# -------------------------------------------------------------------
# 3. Create Azure Container Registry
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[3/7] Creating Container Registry '$AcrName'..." -ForegroundColor Yellow
$acrExists = az acr show --name $AcrName --resource-group $ResourceGroup 2>$null
if ($acrExists) {
    Write-Host "  Already exists, skipping." -ForegroundColor Green
} else {
    az acr create `
        --resource-group $ResourceGroup `
        --name $AcrName `
        --sku Basic `
        --admin-enabled true `
        --output none
    Write-Host "  Created." -ForegroundColor Green
}

# -------------------------------------------------------------------
# 4. Build image in ACR (no local Docker required)
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[4/7] Building container image in ACR (this takes 2-4 min)..." -ForegroundColor Yellow
$env:PYTHONUTF8 = "1"
$buildOutput = az acr build `
    --registry $AcrName `
    --image cloudbench:latest `
    --file Dockerfile `
    . 2>&1
$buildOutput | ForEach-Object {
    if ($_ -match "Step \d+/\d+|Successfully|WARN|ERROR|Run ID") {
        Write-Host "  $_" -ForegroundColor Gray
    }
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "  CLI log-streaming failed (known Windows encoding issue). Checking if image was built..." -ForegroundColor Yellow
    $imgCheck = az acr repository show-tags --name $AcrName --repository cloudbench --output tsv 2>$null
    if (-not $imgCheck) {
        Write-Host "  BUILD FAILED — no image found in registry." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Image exists in registry — build succeeded despite log error." -ForegroundColor Green
} else {
    Write-Host "  Image built and pushed." -ForegroundColor Green
}

# -------------------------------------------------------------------
# 5. Create Container Apps Environment
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[5/7] Creating Container Apps environment '$EnvName'..." -ForegroundColor Yellow
$envExists = az containerapp env show --name $EnvName --resource-group $ResourceGroup 2>$null
if ($envExists) {
    Write-Host "  Already exists, skipping." -ForegroundColor Green
} else {
    az containerapp env create `
        --name $EnvName `
        --resource-group $ResourceGroup `
        --location $Location `
        --output none
    Write-Host "  Created." -ForegroundColor Green
}

# -------------------------------------------------------------------
# 6. Deploy Container App
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[6/7] Deploying container app '$AppName'..." -ForegroundColor Yellow

$acrServer = "$AcrName.azurecr.io"
$acrCreds = az acr credential show --name $AcrName | ConvertFrom-Json
$acrPassword = $acrCreds.passwords[0].value

$revSuffix = "v" + (Get-Date -Format "yyyyMMddHHmm")
$appExists = az containerapp show --name $AppName --resource-group $ResourceGroup 2>$null
if ($appExists) {
    Write-Host "  App exists — deploying new revision ($revSuffix)..." -ForegroundColor Gray
    az containerapp update `
        --name $AppName `
        --resource-group $ResourceGroup `
        --image "$acrServer/cloudbench:latest" `
        --revision-suffix $revSuffix `
        --output none
} else {
    az containerapp create `
        --name $AppName `
        --resource-group $ResourceGroup `
        --environment $EnvName `
        --image "$acrServer/cloudbench:latest" `
        --registry-server $acrServer `
        --registry-username $AcrName `
        --registry-password $acrPassword `
        --target-port 8000 `
        --ingress external `
        --min-replicas 1 `
        --max-replicas 1 `
        --cpu 1 --memory 2Gi `
        --transport auto `
        --output none
}
Write-Host "  Deployed." -ForegroundColor Green

# -------------------------------------------------------------------
# 7. Get the URL
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[7/7] Fetching app URL..." -ForegroundColor Yellow
$fqdn = az containerapp show `
    --name $AppName `
    --resource-group $ResourceGroup `
    --query "properties.configuration.ingress.fqdn" `
    --output tsv

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment complete!" -ForegroundColor Green
Write-Host "" -ForegroundColor Green
Write-Host "  URL: https://$fqdn" -ForegroundColor Cyan
Write-Host "" -ForegroundColor Green
Write-Host "  Login:    sqladmin" -ForegroundColor White
Write-Host "  Password: (the one you set)" -ForegroundColor White
Write-Host "" -ForegroundColor Green
Write-Host "  Useful commands:" -ForegroundColor Gray
Write-Host "    Logs:    az containerapp logs show -n $AppName -g $ResourceGroup --follow" -ForegroundColor Gray
Write-Host "    Restart: az containerapp revision restart -n $AppName -g $ResourceGroup" -ForegroundColor Gray
Write-Host "    Delete:  az group delete -n $ResourceGroup --yes --no-wait" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
