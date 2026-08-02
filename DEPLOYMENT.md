# Docker Deployment Guide

Deploy Data Bench as a container to **Azure**, **Google Cloud**, or **AWS**. The app is a single stateless container, so any container host works — this guide covers the managed, serverless options on each cloud.

> For local development without Docker, see the [Quick Start](README.md#quick-start) in the README.

---

## Before you deploy

A few things that apply to every cloud:

- **Port:** the container listens on **`8000`** (see the [`Dockerfile`](Dockerfile) `EXPOSE`/`CMD`). Point your platform's ingress at `8000`.
- **Login:** the web UI is gated by a placeholder login — **`sqladmin`** / **`cloudbench`**. **Change this before exposing Data Bench publicly** by editing `_AUTH_USER` / `_AUTH_HASH` in [`src/sqlio_cloud/web/app.py`](src/sqlio_cloud/web/app.py) and rebuilding the image. See [Changing the login](#changing-the-login).
- **WebSockets + long runs:** live progress uses a WebSocket, and a Full Stress run can take 30–90 minutes. Enable **session affinity / sticky sessions** and set the **request timeout to ~3600s** so long benchmarks aren't cut off.
- **Outbound network:** Data Bench connects *out* to your database. The container host must be able to reach your database's host and port (1433 SQL Server, 5432 PostgreSQL, 3306 MySQL). No inbound access to your database is required.
- **Firewall allow-listing:** after deploying, open the running app, note the **Data Bench IP address** shown on the connection page, and add it to your target database's firewall rules.
- **Resources:** 1 vCPU / 2 GiB RAM is a good baseline. Benchmarks are driven by the target DB, not this container.

### Build and run locally with Docker

Validate the image before pushing it anywhere:

```bash
# From the repo root
docker build -t databench:latest .

# Run it, exposing container port 8000 as localhost:8080
docker run --rm -p 8080:8000 databench:latest
```

Open http://localhost:8080 and sign in with `sqladmin` / `cloudbench`.

---

## Azure — Container Apps

Azure Container Apps is serverless, supports WebSockets, and handles TLS/ingress for you.

### Option A: one-shot script (Windows / PowerShell)

The repo includes [`deploy-azure.ps1`](deploy-azure.ps1), which creates the resource group, an Azure Container Registry, builds the image in ACR (no local Docker needed), and deploys the Container App:

```powershell
az login
.\deploy-azure.ps1                                  # uses defaults
.\deploy-azure.ps1 -ResourceGroup "my-rg" -AppName "mybench"
```

### Option B: manual (Azure CLI, any OS)

```bash
# Variables
RG=cloudbench-rg
LOCATION=westus2
ACR=cloudbenchacr
ENV=cloudbench-env
APP=cloudbench

az login
az group create --name $RG --location $LOCATION

# Container registry, then build the image inside ACR (no local Docker required)
az acr create --resource-group $RG --name $ACR --sku Basic --admin-enabled true
az acr build --registry $ACR --image cloudbench:latest --file Dockerfile .

# Container Apps environment
az containerapp env create --name $ENV --resource-group $RG --location $LOCATION

# Deploy — target the container's port 8000
ACR_SERVER=$ACR.azurecr.io
ACR_PASSWORD=$(az acr credential show --name $ACR --query "passwords[0].value" -o tsv)

az containerapp create \
  --name $APP --resource-group $RG --environment $ENV \
  --image "$ACR_SERVER/cloudbench:latest" \
  --registry-server $ACR_SERVER --registry-username $ACR --registry-password "$ACR_PASSWORD" \
  --target-port 8000 --ingress external \
  --min-replicas 1 --max-replicas 1 \
  --cpu 1 --memory 2Gi --transport auto

# Get the URL
az containerapp show --name $APP --resource-group $RG \
  --query "properties.configuration.ingress.fqdn" -o tsv
```

Keeping `--min-replicas 1 --max-replicas 1` (single replica) sidesteps the need for sticky sessions, since all WebSocket traffic hits the same instance.

**Manage / tear down:**

```bash
az containerapp logs show -n $APP -g $RG --follow      # logs
az containerapp revision restart -n $APP -g $RG        # restart
az group delete -n $RG --yes --no-wait                 # delete everything
```

---

## Google Cloud — Cloud Run

Cloud Run is serverless, supports WebSockets with session affinity, and issues an HTTPS URL automatically.

### Option A: one-shot script (Windows / PowerShell)

The repo includes [`deploy-gcp.ps1`](deploy-gcp.ps1), which enables the required APIs, builds via Cloud Build, and deploys to Cloud Run:

```powershell
gcloud auth login
.\deploy-gcp.ps1                                        # uses defaults
.\deploy-gcp.ps1 -ProjectId "my-project" -Region "us-central1"
```

### Option B: manual (gcloud CLI, any OS)

```bash
# Variables
PROJECT_ID=my-project
REGION=us-central1
SERVICE=databench
REPO=databench

gcloud config set project $PROJECT_ID
gcloud services enable cloudbuild.googleapis.com run.googleapis.com artifactregistry.googleapis.com

# Artifact Registry repo, then build via Cloud Build (no local Docker required)
gcloud artifacts repositories create $REPO --repository-format docker --location $REGION
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/databench:latest"
gcloud builds submit --tag $IMAGE

# Deploy — port 8000, sticky sessions + long timeout for WebSockets/long runs
gcloud run deploy $SERVICE \
  --image $IMAGE --region $REGION \
  --port 8000 --allow-unauthenticated \
  --min-instances 0 --max-instances 2 \
  --cpu 1 --memory 2Gi \
  --timeout 3600 --session-affinity

# Get the URL
gcloud run services describe $SERVICE --region $REGION --format 'value(status.url)'
```

`--session-affinity` keeps a client pinned to one instance (needed for the progress WebSocket), and `--timeout 3600` allows long benchmark runs to complete.

**Manage / tear down:**

```bash
gcloud run services logs tail $SERVICE --region $REGION       # stream logs
gcloud run services delete $SERVICE --region $REGION          # delete
```

---

## AWS — App Runner (via ECR)

AWS App Runner runs a container directly from an ECR image, manages TLS/ingress, and supports WebSockets. (There's no PowerShell helper script for AWS — use the CLI steps below.)

```bash
# Variables
AWS_REGION=us-east-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO=databench
ECR_URI="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO"

# 1. Create the ECR repository
aws ecr create-repository --repository-name $REPO --region $AWS_REGION

# 2. Authenticate Docker to ECR
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# 3. Build for linux/amd64 and push (use buildx if you're on Apple Silicon)
docker buildx build --platform linux/amd64 -t "$ECR_URI:latest" --push .
```

Then create the App Runner service pointing at that image on **port 8000**. Easiest via the console (App Runner → Create service → Container registry → your ECR image → port `8000`), or with the CLI:

```bash
aws apprunner create-service \
  --service-name databench \
  --region $AWS_REGION \
  --source-configuration '{
    "AuthenticationConfiguration": {"AccessRoleArn": "arn:aws:iam::'"$ACCOUNT_ID"':role/AppRunnerECRAccessRole"},
    "AutoDeploymentsEnabled": false,
    "ImageRepository": {
      "ImageIdentifier": "'"$ECR_URI"':latest",
      "ImageRepositoryType": "ECR",
      "ImageConfiguration": {"Port": "8000"}
    }
  }' \
  --instance-configuration '{"Cpu": "1024", "Memory": "2048"}' \
  --health-check-configuration '{"Protocol": "TCP"}'
```

> `AppRunnerECRAccessRole` is an IAM role that lets App Runner pull from ECR (trust policy for `build.apprunner.amazonaws.com`, with the `AWSAppRunnerServicePolicyForECRAccess` managed policy). Create it once; the console can create it for you automatically.

App Runner returns a service URL once the deploy completes:

```bash
aws apprunner list-services --region $AWS_REGION \
  --query "ServiceSummaryList[?ServiceName=='databench'].ServiceUrl" --output text
```

**Alternative — ECS Fargate + ALB:** if you need VPC networking (e.g., to reach a database on a private subnet) or finer control, run the same image as an ECS Fargate task behind an Application Load Balancer. Target group port `8000`, enable stickiness on the target group for WebSockets, and place the task in a subnet with a route to your database.

**Manage / tear down:**

```bash
aws apprunner list-operations --service-arn <arn> --region $AWS_REGION   # deploy status
aws apprunner delete-service --service-arn <arn> --region $AWS_REGION    # delete
aws ecr delete-repository --repository-name $REPO --force --region $AWS_REGION
```

---

## Changing the login

The default `sqladmin` / `cloudbench` login is a placeholder. To change it, compute a new salted hash and rebuild the image:

```bash
python3 -c "import hashlib; print(hashlib.sha256(('cloudbench_v1' + 'YOUR_NEW_PASSWORD').encode()).hexdigest())"
```

Set the result as `_AUTH_HASH` (and optionally change `_AUTH_USER`) in [`src/sqlio_cloud/web/app.py`](src/sqlio_cloud/web/app.py), then rebuild and redeploy. For a real multi-user deployment, replace the placeholder auth with a proper identity provider (see the roadmap in [DESIGN.md](DESIGN.md)).

---

## After deploying

1. Open the app URL and sign in.
2. On the connection page, copy the **Data Bench IP address** and add it to your target database's firewall / authorized networks.
3. Point Data Bench at a **dedicated test database** — never production (see [Before you start](README.md#before-you-start)).
4. When you're done, **delete the service** to avoid ongoing charges (see the tear-down commands above).
