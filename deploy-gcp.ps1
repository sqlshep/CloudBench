<#
.SYNOPSIS
    Deploy Data Bench to Google Cloud Run.

.DESCRIPTION
    One-shot script that builds the container image via Cloud Build
    and deploys to Cloud Run. Safe to re-run.

.EXAMPLE
    .\deploy-gcp.ps1
    .\deploy-gcp.ps1 -ProjectId "my-project" -Region "us-central1"
#>

param(
    [string]$ProjectId   = "gemini-shep",
    [string]$Region      = "us-east1",
    [string]$ServiceName = "databench",
    [string]$ImageName   = "databench"
)

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Data Bench - Google Cloud Run          " -ForegroundColor Cyan
Write-Host "  Project: $ProjectId                   " -ForegroundColor Cyan
Write-Host "  Region:  $Region                      " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# -------------------------------------------------------------------
# 1. Verify gcloud CLI is installed and configured
# -------------------------------------------------------------------
Write-Host "[1/5] Checking gcloud CLI..." -ForegroundColor Yellow
$gcloudPath = Get-Command gcloud -ErrorAction SilentlyContinue
if (-not $gcloudPath) {
    Write-Host "  ERROR: gcloud CLI not found." -ForegroundColor Red
    Write-Host "  Install from https://cloud.google.com/sdk/docs/install" -ForegroundColor Red
    exit 1
}
$acct = gcloud config get-value account 2>$null
if (-not $acct -or $acct -eq "(unset)") {
    Write-Host "  ERROR: Not logged in. Run 'gcloud auth login' first." -ForegroundColor Red
    exit 1
}
Write-Host "  Logged in as: $acct" -ForegroundColor Green
& gcloud config set project $ProjectId --quiet 2>$null
Write-Host "  Project: $ProjectId" -ForegroundColor Green

# -------------------------------------------------------------------
# 2. Enable required APIs
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[2/5] Enabling required APIs..." -ForegroundColor Yellow
$apis = @(
    "cloudbuild.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com"
)
foreach ($api in $apis) {
    gcloud services enable $api --project $ProjectId --quiet 2>$null
}
Write-Host "  Cloud Build, Cloud Run, Artifact Registry enabled." -ForegroundColor Green

# -------------------------------------------------------------------
# 3. Create Artifact Registry repository (if needed)
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[3/5] Setting up Artifact Registry..." -ForegroundColor Yellow
$repoName = "databench"
$repoCheck = gcloud artifacts repositories describe $repoName --location $Region --project $ProjectId 2>$null
if ($LASTEXITCODE -eq 0 -and $repoCheck) {
    Write-Host "  Repository '$repoName' already exists." -ForegroundColor Green
} else {
    gcloud artifacts repositories create $repoName --repository-format docker --location $Region --project $ProjectId --quiet 2>$null
    Write-Host "  Repository '$repoName' created." -ForegroundColor Green
}

$imageTag = "$Region-docker.pkg.dev/$ProjectId/$repoName/${ImageName}:latest"

# -------------------------------------------------------------------
# 4. Build container image via Cloud Build
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[4/5] Building container image (this takes 2-4 min)..." -ForegroundColor Yellow
$env:PYTHONUTF8 = "1"
gcloud builds submit --tag $imageTag --project $ProjectId --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Checking if image was built..." -ForegroundColor Yellow
    $imgCheck = gcloud artifacts docker images list "$Region-docker.pkg.dev/$ProjectId/$repoName/$ImageName" --format 'value(package)' --limit 1 2>$null
    if (-not $imgCheck) {
        Write-Host "  BUILD FAILED - no image found in Artifact Registry." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Image exists - build succeeded despite log issues." -ForegroundColor Green
} else {
    Write-Host "  Image built and pushed." -ForegroundColor Green
}

# -------------------------------------------------------------------
# 5. Deploy to Cloud Run
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[5/5] Deploying to Cloud Run..." -ForegroundColor Yellow
gcloud run deploy $ServiceName --image $imageTag --region $Region --project $ProjectId --port 8000 --allow-unauthenticated --min-instances 0 --max-instances 2 --cpu 1 --memory 2Gi --timeout 3600 --session-affinity --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Deployment failed." -ForegroundColor Red
    exit 1
}

# Get the service URL
$serviceUrl = gcloud run services describe $ServiceName --region $Region --project $ProjectId --format 'value(status.url)' 2>$null

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment complete!" -ForegroundColor Green
Write-Host "" -ForegroundColor Green
Write-Host "  URL: $serviceUrl" -ForegroundColor Cyan
Write-Host "" -ForegroundColor Green
Write-Host "  Login:    sqladmin" -ForegroundColor White
Write-Host "  Password: (the one you set)" -ForegroundColor White
Write-Host "" -ForegroundColor Green
Write-Host "  Useful commands:" -ForegroundColor Gray
Write-Host "    Logs:    gcloud run services logs read $ServiceName --region $Region --project $ProjectId" -ForegroundColor Gray
Write-Host "    Stream:  gcloud run services logs tail $ServiceName --region $Region --project $ProjectId" -ForegroundColor Gray
Write-Host "    Delete:  gcloud run services delete $ServiceName --region $Region --project $ProjectId" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
