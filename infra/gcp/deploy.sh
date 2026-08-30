#!/usr/bin/env bash
# Build one immutable image and deploy all four Agentic Agriculture Cloud Run roles.
# Long-lived resources and secrets must already exist; run bootstrap.sh first.
#
# Required:
#   export GCP_PROJECT_ID="your-project-id"
# Optional:
#   export GCP_REGION="southamerica-east1"
#   export AA_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
#   export AA_SKIP_BUILD=true       # reuse the resolved AA_IMAGE_URI/tag
#   export AA_IMAGE_URI="region-docker.pkg.dev/project/repo/app:tag"

set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export LC_ALL=C
# Git Bash on Windows otherwise rewrites Cloud Run URL paths such as
# /healthz and /mcp into local C:/Program Files/Git paths. Exclude only the
# affected gcloud arguments so its own bundled-Python path still converts.
export MSYS2_ARG_CONV_EXCL='--startup-probe=;--args=;--set-env-vars=;--update-env-vars='

readonly SCRIPT_NAME="${0##*/}"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"

log() {
    printf '[%s] %s\n' "$SCRIPT_NAME" "$*"
}

die() {
    printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2
    exit 1
}

on_error() {
    local exit_code=$?
    printf '[%s] ERROR: command failed at line %s (exit %s).\n' \
        "$SCRIPT_NAME" "${BASH_LINENO[0]}" "$exit_code" >&2
    exit "$exit_code"
}
trap on_error ERR

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

require_command gcloud
require_command git

PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-southamerica-east1}"
ARTIFACT_REPOSITORY="${AA_ARTIFACT_REPOSITORY:-agentic-agriculture}"
ARTIFACT_BUCKET="${AA_ARTIFACT_BUCKET:-${PROJECT_ID}-agentic-agriculture-artifacts}"
TASK_QUEUE="${AA_TASK_QUEUE:-sentinel-analysis}"

WEB_SERVICE="${AA_WEB_SERVICE:-agentic-agriculture-web}"
WORKER_SERVICE="${AA_WORKER_SERVICE:-agentic-agriculture-worker}"
MCP_SERVICE="${AA_MCP_SERVICE:-agentic-agriculture-mcp}"
AGENT_SERVICE="${AA_AGENT_SERVICE:-agentic-agriculture-agent}"

WEB_SA_ID="${AA_WEB_SERVICE_ACCOUNT_ID:-aa-web}"
WORKER_SA_ID="${AA_WORKER_SERVICE_ACCOUNT_ID:-aa-worker}"
MCP_SA_ID="${AA_MCP_SERVICE_ACCOUNT_ID:-aa-mcp}"
AGENT_SA_ID="${AA_AGENT_SERVICE_ACCOUNT_ID:-aa-agent}"
TASK_INVOKER_SA_ID="${AA_TASK_INVOKER_SERVICE_ACCOUNT_ID:-aa-task-invoker}"

DJANGO_SECRET_ID="${AA_DJANGO_SECRET_ID:-agentic-agriculture-django-secret-key}"
DEMO_PASSWORD_SECRET_ID="${AA_DEMO_PASSWORD_SECRET_ID:-agentic-agriculture-demo-password-hash}"
TASK_SECRET_ID="${AA_TASK_SECRET_ID:-agentic-agriculture-task-shared-secret}"

DEMO_USERNAME="${AA_DEMO_USERNAME:-demo}"
PRODUCT_NAME="${AA_PRODUCT_NAME:-Agentic Agriculture}"
AGENT_MODEL="${AA_AGENT_MODEL:-gemini-3.5-flash}"
SKIP_BUILD="${AA_SKIP_BUILD:-false}"

[[ -n "$PROJECT_ID" ]] || die "Set GCP_PROJECT_ID before running this script."
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || die "GCP_PROJECT_ID is invalid."
[[ "$REGION" =~ ^[a-z]+[a-z0-9-]*[0-9]$ ]] || die "GCP_REGION is invalid."
[[ "$SKIP_BUILD" == "true" || "$SKIP_BUILD" == "false" ]] || \
    die "AA_SKIP_BUILD must be true or false."

# The alternate gcloud dictionary delimiter below is '|'. Reject it in values
# controlled by the caller so an environment value cannot inject another key.
for value in \
    "$PROJECT_ID" "$REGION" "$ARTIFACT_REPOSITORY" "$ARTIFACT_BUCKET" "$TASK_QUEUE" \
    "$DEMO_USERNAME" "$PRODUCT_NAME" "$AGENT_MODEL"; do
    [[ "$value" != *'|'* && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || \
        die "Deployment values cannot contain '|', CR, or LF."
done

for service_name in "$WEB_SERVICE" "$WORKER_SERVICE" "$MCP_SERVICE" "$AGENT_SERVICE"; do
    [[ "$service_name" =~ ^[a-z][a-z0-9-]{0,47}[a-z0-9]$ ]] || \
        die "Invalid Cloud Run service name: $service_name"
done

ACTIVE_ACCOUNT="$(gcloud auth list --filter='status:ACTIVE' --format='value(account)' | head -n 1)"
[[ -n "$ACTIVE_ACCOUNT" ]] || die "No active gcloud account. Run: gcloud auth login"
gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null

WEB_SA="${WEB_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="${WORKER_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
MCP_SA="${MCP_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
AGENT_SA="${AGENT_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
TASK_INVOKER_SA="${TASK_INVOKER_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

for account_email in "$WEB_SA" "$WORKER_SA" "$MCP_SA" "$AGENT_SA" "$TASK_INVOKER_SA"; do
    gcloud iam service-accounts describe "$account_email" \
        --project="$PROJECT_ID" --format='value(email)' >/dev/null || \
        die "Missing service account $account_email; run bootstrap.sh first."
done

gcloud artifacts repositories describe "$ARTIFACT_REPOSITORY" \
    --project="$PROJECT_ID" --location="$REGION" --format='value(name)' >/dev/null || \
    die "Missing Artifact Registry repository; run bootstrap.sh first."
gcloud storage buckets describe "gs://${ARTIFACT_BUCKET}" \
    --project="$PROJECT_ID" --format='value(name)' >/dev/null || \
    die "Missing artifact bucket; run bootstrap.sh first."
gcloud tasks queues describe "$TASK_QUEUE" \
    --project="$PROJECT_ID" --location="$REGION" --format='value(name)' >/dev/null || \
    die "Missing Cloud Tasks queue; run bootstrap.sh first."
secret_version_number() {
    local secret_id=$1
    enabled_version="$(
        gcloud secrets versions list "$secret_id" \
            --project="$PROJECT_ID" \
            --filter='state=ENABLED' \
            --limit=1 \
            --sort-by='~createTime' \
            --format='value(name)'
    )"
    [[ -n "$enabled_version" ]] || \
        die "Secret $secret_id has no enabled version; run bootstrap.sh first."
    printf '%s' "${enabled_version##*/}"
}

DJANGO_SECRET_VERSION="$(secret_version_number "$DJANGO_SECRET_ID")"
DEMO_PASSWORD_SECRET_VERSION="$(secret_version_number "$DEMO_PASSWORD_SECRET_ID")"
TASK_SECRET_VERSION="$(secret_version_number "$TASK_SECRET_ID")"

if [[ -n "${AA_IMAGE_URI:-}" ]]; then
    IMAGE_URI="$AA_IMAGE_URI"
else
    IMAGE_TAG="${AA_IMAGE_TAG:-$(git -C "$REPOSITORY_ROOT" rev-parse --short=12 HEAD)}"
    [[ "$IMAGE_TAG" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || die "AA_IMAGE_TAG is invalid."
    IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/app:${IMAGE_TAG}"
fi
[[ "$IMAGE_URI" != *'|'* && "$IMAGE_URI" != *','* && "$IMAGE_URI" != *$'\n'* ]] || \
    die "AA_IMAGE_URI contains unsupported characters."

if [[ "$SKIP_BUILD" == "true" ]]; then
    log "Skipping build and verifying existing image: $IMAGE_URI"
    gcloud artifacts docker images describe "$IMAGE_URI" \
        --project="$PROJECT_ID" --format='value(image_summary.digest)' >/dev/null
else
    if gcloud artifacts docker images describe "$IMAGE_URI" \
        --project="$PROJECT_ID" --format='value(image_summary.digest)' >/dev/null 2>&1; then
        log "Reusing existing immutable image: $IMAGE_URI"
    else
        log "Building one immutable image for all service roles: $IMAGE_URI"
        gcloud builds submit "$REPOSITORY_ROOT" \
            --project="$PROJECT_ID" \
            --region="$REGION" \
            --tag="$IMAGE_URI" \
            --quiet
    fi
fi

service_url() {
    local service_name=$1
    gcloud run services describe "$service_name" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --format='value(status.url)'
}

add_run_invoker() {
    local service_name=$1
    local member=$2
    gcloud run services add-iam-policy-binding "$service_name" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --member="$member" \
        --role=roles/run.invoker \
        --condition=None \
        --quiet >/dev/null
}

existing_worker_url="$(service_url "$WORKER_SERVICE" 2>/dev/null || true)"
worker_target_url="${existing_worker_url:-https://placeholder.invalid}"

# Both web and worker import Django's production settings. Keeping the entire
# backend matrix explicit makes revisions inspectable and avoids accidental
# fallback if application defaults change later.
COMMON_DJANGO_ENV="^|^APP_ENV=production|DJANGO_DEBUG=false|DJANGO_ALLOWED_HOSTS=.run.app"
COMMON_DJANGO_ENV+="|DJANGO_TIME_ZONE=America/Sao_Paulo|PRODUCT_NAME=${PRODUCT_NAME}"
COMMON_DJANGO_ENV+="|PERSISTENCE_BACKEND=firestore|ARTIFACT_BACKEND=gcs|TASK_BACKEND=cloud_tasks"
COMMON_DJANGO_ENV+="|BOUNDARY_BACKEND=geospatial|ANALYSIS_PIPELINE_BACKEND=sentinel"
COMMON_DJANGO_ENV+="|ANALYSIS_TARGET_SCENE_COUNT=6|ANALYSIS_MAX_DIMENSION=512|ANALYSIS_DAILY_LIMIT=3"
COMMON_DJANGO_ENV+="|GOOGLE_CLOUD_PROJECT=${PROJECT_ID}|FIRESTORE_DATABASE=(default)"
COMMON_DJANGO_ENV+="|GCS_BUCKET=${ARTIFACT_BUCKET}|CLOUD_TASKS_LOCATION=${REGION}"
COMMON_DJANGO_ENV+="|CLOUD_TASKS_QUEUE=${TASK_QUEUE}|CLOUD_TASKS_BASE_URL=${worker_target_url}"
COMMON_DJANGO_ENV+="|CLOUD_TASKS_SERVICE_ACCOUNT=${TASK_INVOKER_SA}"
COMMON_DJANGO_ENV+="|CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS=900"
COMMON_DJANGO_ENV+="|DJANGO_COOKIE_SECURE=true|DJANGO_SECURE_SSL_REDIRECT=true"
COMMON_DJANGO_ENV+="|DJANGO_SECURE_HSTS_SECONDS=31536000"

log "Deploying the private Sentinel worker."
gcloud run deploy "$WORKER_SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --image="$IMAGE_URI" \
    --service-account="$WORKER_SA" \
    --execution-environment=gen2 \
    --port=8080 \
    --cpu=2 \
    --memory=4Gi \
    --concurrency=1 \
    --min-instances=0 \
    --max-instances=1 \
    --timeout=900 \
    --ingress=all \
    --no-allow-unauthenticated \
    --set-env-vars="${COMMON_DJANGO_ENV}|WEB_CONCURRENCY=1|GUNICORN_THREADS=1|GUNICORN_TIMEOUT=840" \
    --set-secrets="DJANGO_SECRET_KEY=${DJANGO_SECRET_ID}:${DJANGO_SECRET_VERSION},CLOUD_TASKS_SHARED_SECRET=${TASK_SECRET_ID}:${TASK_SECRET_VERSION}" \
    --startup-probe='initialDelaySeconds=0,timeoutSeconds=3,periodSeconds=5,failureThreshold=24,tcpSocket.port=8080' \
    --labels='app=agentic-agriculture,component=worker,managed-by=infra-script' \
    --description='Private Sentinel-2 temporal analysis worker' \
    --deploy-health-check \
    --quiet

WORKER_URL="$(service_url "$WORKER_SERVICE")"
WORKER_URL="${WORKER_URL%/}"
[[ "$WORKER_URL" == https://*.run.app ]] || die "Unexpected worker URL: $WORKER_URL"

# The first deployment is needed to discover the canonical run.app origin.
# Replace the harmless placeholder immediately; this also makes OIDC audience
# and task destination identical, as required by Cloud Run authentication.
gcloud run services update "$WORKER_SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --update-env-vars="CLOUD_TASKS_BASE_URL=${WORKER_URL}" \
    --quiet >/dev/null

log "Deploying the private geospatial MCP service."
gcloud run deploy "$MCP_SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --image="$IMAGE_URI" \
    --service-account="$MCP_SA" \
    --execution-environment=gen2 \
    --port=8080 \
    --cpu=1 \
    --memory=512Mi \
    --concurrency=20 \
    --min-instances=0 \
    --max-instances=2 \
    --timeout=60 \
    --ingress=all \
    --no-allow-unauthenticated \
    --command=python \
    --args=-m,geospatial.mcp_server \
    --set-env-vars='^|^MCP_HOST=0.0.0.0' \
    --clear-secrets \
    --startup-probe='initialDelaySeconds=0,timeoutSeconds=3,periodSeconds=5,failureThreshold=24,tcpSocket.port=8080' \
    --labels='app=agentic-agriculture,component=mcp,managed-by=infra-script' \
    --description='Private read-only Sentinel-2 MCP tools' \
    --deploy-health-check \
    --quiet

MCP_URL="$(service_url "$MCP_SERVICE")"
MCP_URL="${MCP_URL%/}"
[[ "$MCP_URL" == https://*.run.app ]] || die "Unexpected MCP URL: $MCP_URL"

log "Deploying the private Gemini and Google ADK service."
AGENT_ENV="^|^APP_ENV=production|DJANGO_DEBUG=false|DJANGO_ALLOWED_HOSTS=.run.app"
AGENT_ENV+="|GOOGLE_CLOUD_PROJECT=${PROJECT_ID}|FIRESTORE_DATABASE=(default)"
AGENT_ENV+="|PERSISTENCE_BACKEND=firestore|GOOGLE_GENAI_USE_VERTEXAI=true"
AGENT_ENV+="|GOOGLE_CLOUD_LOCATION=global|AGENT_MODEL=${AGENT_MODEL}"
AGENT_ENV+="|AGENT_APP_NAME=agentic_agriculture|AGENT_MCP_ENABLED=true"
AGENT_ENV+="|AGENT_MCP_URL=${MCP_URL}/mcp|AGENT_MCP_AUDIENCE=${MCP_URL}"
AGENT_ENV+="|AGENT_MCP_TIMEOUT_SECONDS=15|AGENT_MCP_TOOL_CACHE_SECONDS=300"
gcloud run deploy "$AGENT_SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --image="$IMAGE_URI" \
    --service-account="$AGENT_SA" \
    --execution-environment=gen2 \
    --port=8080 \
    --cpu=1 \
    --memory=1Gi \
    --concurrency=4 \
    --min-instances=0 \
    --max-instances=1 \
    --timeout=300 \
    --ingress=all \
    --no-allow-unauthenticated \
    --command=adk \
    --args="api_server,--host=0.0.0.0,--port=8080,--no-reload,--session_service_uri=memory://,--artifact_service_uri=gs://${ARTIFACT_BUCKET},--auto_create_session,agentic_agriculture" \
    --set-env-vars="$AGENT_ENV" \
    --set-secrets="DJANGO_SECRET_KEY=${DJANGO_SECRET_ID}:${DJANGO_SECRET_VERSION}" \
    --startup-probe='initialDelaySeconds=0,timeoutSeconds=3,periodSeconds=5,failureThreshold=36,tcpSocket.port=8080' \
    --labels='app=agentic-agriculture,component=agent,managed-by=infra-script' \
    --description='Private Gemini 3.5+ Google ADK multi-agent API' \
    --deploy-health-check \
    --quiet

AGENT_URL="$(service_url "$AGENT_SERVICE")"
AGENT_URL="${AGENT_URL%/}"
[[ "$AGENT_URL" == https://*.run.app ]] || die "Unexpected agent URL: $AGENT_URL"

log "Deploying the public, login-protected web service."
WEB_ENV="${COMMON_DJANGO_ENV}|DEMO_USERNAME=${DEMO_USERNAME}"
gcloud run deploy "$WEB_SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --image="$IMAGE_URI" \
    --service-account="$WEB_SA" \
    --execution-environment=gen2 \
    --port=8080 \
    --cpu=1 \
    --memory=1Gi \
    --concurrency=20 \
    --min-instances=0 \
    --max-instances=2 \
    --timeout=300 \
    --ingress=all \
    --allow-unauthenticated \
    --set-env-vars="${WEB_ENV}|WEB_CONCURRENCY=2|GUNICORN_THREADS=4|GUNICORN_TIMEOUT=840" \
    --set-secrets="DJANGO_SECRET_KEY=${DJANGO_SECRET_ID}:${DJANGO_SECRET_VERSION},DEMO_PASSWORD_HASH=${DEMO_PASSWORD_SECRET_ID}:${DEMO_PASSWORD_SECRET_VERSION},CLOUD_TASKS_SHARED_SECRET=${TASK_SECRET_ID}:${TASK_SECRET_VERSION}" \
    --startup-probe='initialDelaySeconds=0,timeoutSeconds=3,periodSeconds=5,failureThreshold=24,tcpSocket.port=8080' \
    --labels='app=agentic-agriculture,component=web,managed-by=infra-script' \
    --description='Public Agentic Agriculture demonstration web application' \
    --deploy-health-check \
    --quiet

WEB_URL="$(service_url "$WEB_SERVICE")"
WEB_URL="${WEB_URL%/}"
[[ "$WEB_URL" == https://*.run.app ]] || die "Unexpected web URL: $WEB_URL"
WEB_HOST="${WEB_URL#https://}"

gcloud run services update "$WEB_SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --update-env-vars="^|^DJANGO_ALLOWED_HOSTS=${WEB_HOST}|DJANGO_CSRF_TRUSTED_ORIGINS=${WEB_URL}|CLOUD_TASKS_BASE_URL=${WORKER_URL}" \
    --quiet >/dev/null

log "Applying Cloud Run service-to-service invocation boundaries."
add_run_invoker "$WORKER_SERVICE" "serviceAccount:${TASK_INVOKER_SA}"
add_run_invoker "$MCP_SERVICE" "serviceAccount:${AGENT_SA}"

# A new revision may take a moment to become the observed latest ready revision.
for service_name in "$WEB_SERVICE" "$WORKER_SERVICE" "$MCP_SERVICE" "$AGENT_SERVICE"; do
    ready_revision="$(
        gcloud run services describe "$service_name" \
            --project="$PROJECT_ID" \
            --region="$REGION" \
            --format='value(status.latestReadyRevisionName)'
    )"
    [[ -n "$ready_revision" ]] || die "Service has no ready revision: $service_name"
done

log "Deployment complete. All roles use the same immutable image."
printf '%s\n' \
    "Image: $IMAGE_URI" \
    "Web (public): $WEB_URL" \
    "Worker (private): $WORKER_URL" \
    "MCP (private): $MCP_URL" \
    "Agent (private): $AGENT_URL" \
    "Next: bash infra/gcp/smoke.sh"
