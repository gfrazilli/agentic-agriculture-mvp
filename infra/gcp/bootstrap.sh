#!/usr/bin/env bash
# Provision the long-lived Google Cloud resources used by Agentic Agriculture.
#
# Run from Google Cloud Shell after selecting a billed project:
#   export GCP_PROJECT_ID="your-project-id"
#   export DEMO_PASSWORD_HASH_FILE="$HOME/demo-password-hash.txt"
#   bash infra/gcp/bootstrap.sh
#
# Secret inputs are accepted through *_FILE (preferred) or *_VALUE variables.
# Django and Cloud Tasks secrets are generated when they do not exist and no
# input is supplied. The demo password hash is never generated or printed.

set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export LC_ALL=C

readonly SCRIPT_NAME="${0##*/}"

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
require_command grep
require_command tr

PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-southamerica-east1}"
ARTIFACT_REPOSITORY="${AA_ARTIFACT_REPOSITORY:-agentic-agriculture}"
ARTIFACT_BUCKET="${AA_ARTIFACT_BUCKET:-${PROJECT_ID}-agentic-agriculture-artifacts}"
TASK_QUEUE="${AA_TASK_QUEUE:-sentinel-analysis}"

WEB_SA_ID="${AA_WEB_SERVICE_ACCOUNT_ID:-aa-web}"
WORKER_SA_ID="${AA_WORKER_SERVICE_ACCOUNT_ID:-aa-worker}"
MCP_SA_ID="${AA_MCP_SERVICE_ACCOUNT_ID:-aa-mcp}"
AGENT_SA_ID="${AA_AGENT_SERVICE_ACCOUNT_ID:-aa-agent}"
TASK_INVOKER_SA_ID="${AA_TASK_INVOKER_SERVICE_ACCOUNT_ID:-aa-task-invoker}"

DJANGO_SECRET_ID="${AA_DJANGO_SECRET_ID:-agentic-agriculture-django-secret-key}"
DEMO_PASSWORD_SECRET_ID="${AA_DEMO_PASSWORD_SECRET_ID:-agentic-agriculture-demo-password-hash}"
TASK_SECRET_ID="${AA_TASK_SECRET_ID:-agentic-agriculture-task-shared-secret}"
RESEND_SECRET_ID="${AA_RESEND_SECRET_ID:-agentic-agriculture-resend-api-key}"
TURNSTILE_SECRET_ID="${AA_TURNSTILE_SECRET_ID:-agentic-agriculture-turnstile-secret-key}"
CONTACT_RECIPIENT_SECRET_ID="${AA_CONTACT_RECIPIENT_SECRET_ID:-agentic-agriculture-contact-to-email}"
ROTATE_SECRETS="${AA_ROTATE_SECRETS:-false}"

[[ -n "$PROJECT_ID" ]] || die "Set GCP_PROJECT_ID before running this script."
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || die "GCP_PROJECT_ID is invalid."
[[ "$REGION" =~ ^[a-z]+[a-z0-9-]*[0-9]$ ]] || die "GCP_REGION is invalid."
[[ "$ARTIFACT_REPOSITORY" =~ ^[a-z][a-z0-9._-]{2,62}$ ]] || \
    die "AA_ARTIFACT_REPOSITORY is invalid."
[[ "$ARTIFACT_BUCKET" =~ ^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$ ]] || \
    die "AA_ARTIFACT_BUCKET is not a valid Cloud Storage bucket name."
[[ "$TASK_QUEUE" =~ ^[a-zA-Z][a-zA-Z0-9_-]{0,99}$ ]] || die "AA_TASK_QUEUE is invalid."
[[ "$ROTATE_SECRETS" == "true" || "$ROTATE_SECRETS" == "false" ]] || \
    die "AA_ROTATE_SECRETS must be true or false."

for account_id in \
    "$WEB_SA_ID" \
    "$WORKER_SA_ID" \
    "$MCP_SA_ID" \
    "$AGENT_SA_ID" \
    "$TASK_INVOKER_SA_ID"; do
    [[ "$account_id" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || \
        die "Invalid service-account ID: $account_id"
done

ACTIVE_ACCOUNT="$(gcloud auth list --filter='status:ACTIVE' --format='value(account)' | head -n 1)"
[[ -n "$ACTIVE_ACCOUNT" ]] || die "No active gcloud account. Run: gcloud auth login"
gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null

log "Enabling required APIs in project $PROJECT_ID."
readonly -a REQUIRED_APIS=(
    artifactregistry.googleapis.com
    aiplatform.googleapis.com
    cloudbuild.googleapis.com
    cloudtasks.googleapis.com
    compute.googleapis.com
    firestore.googleapis.com
    iam.googleapis.com
    iamcredentials.googleapis.com
    run.googleapis.com
    secretmanager.googleapis.com
    serviceusage.googleapis.com
    storage.googleapis.com
    sts.googleapis.com
    billingbudgets.googleapis.com
)
gcloud services enable "${REQUIRED_APIS[@]}" --project="$PROJECT_ID" --quiet

PROJECT_NUMBER="$(
    gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)'
)"
[[ "$PROJECT_NUMBER" =~ ^[0-9]+$ ]] || die "Could not resolve the Google Cloud project number."

WEB_SA="${WEB_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="${WORKER_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
MCP_SA="${MCP_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
AGENT_SA="${AGENT_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
TASK_INVOKER_SA="${TASK_INVOKER_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
TASKS_SERVICE_AGENT="service-${PROJECT_NUMBER}@gcp-sa-cloudtasks.iam.gserviceaccount.com"

ensure_service_account() {
    local account_id=$1
    local display_name=$2
    local account_email="${account_id}@${PROJECT_ID}.iam.gserviceaccount.com"

    if gcloud iam service-accounts describe "$account_email" \
        --project="$PROJECT_ID" --format='value(email)' >/dev/null 2>&1; then
        log "Service account already exists: $account_email"
        return
    fi

    log "Creating service account: $account_email"
    gcloud iam service-accounts create "$account_id" \
        --project="$PROJECT_ID" \
        --display-name="$display_name" \
        --quiet
}

ensure_service_account "$WEB_SA_ID" "Agentic Agriculture web runtime"
ensure_service_account "$WORKER_SA_ID" "Agentic Agriculture Sentinel worker"
ensure_service_account "$MCP_SA_ID" "Agentic Agriculture private MCP"
ensure_service_account "$AGENT_SA_ID" "Agentic Agriculture Gemini ADK"
ensure_service_account "$TASK_INVOKER_SA_ID" "Agentic Agriculture Cloud Tasks caller"

log "Ensuring the Artifact Registry Docker repository."
if gcloud artifacts repositories describe "$ARTIFACT_REPOSITORY" \
    --project="$PROJECT_ID" --location="$REGION" --format='value(name)' >/dev/null 2>&1; then
    repository_format="$(
        gcloud artifacts repositories describe "$ARTIFACT_REPOSITORY" \
            --project="$PROJECT_ID" --location="$REGION" --format='value(format)'
    )"
    [[ "$repository_format" == "DOCKER" ]] || \
        die "Artifact Registry repository exists but is not a Docker repository."
else
    gcloud artifacts repositories create "$ARTIFACT_REPOSITORY" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --repository-format=docker \
        --description="Agentic Agriculture immutable application images" \
        --labels='app=agentic-agriculture,managed-by=bootstrap' \
        --quiet
fi

log "Ensuring the private artifact bucket: gs://$ARTIFACT_BUCKET"
if gcloud storage buckets describe "gs://${ARTIFACT_BUCKET}" \
    --project="$PROJECT_ID" --format='value(name)' >/dev/null 2>&1; then
    bucket_location="$(
        gcloud storage buckets describe "gs://${ARTIFACT_BUCKET}" \
            --project="$PROJECT_ID" --format='value(location)' | tr '[:upper:]' '[:lower:]'
    )"
    [[ "$bucket_location" == "$REGION" ]] || \
        die "Existing bucket is in $bucket_location, expected $REGION. Bucket locations are immutable."
    gcloud storage buckets update "gs://${ARTIFACT_BUCKET}" \
        --project="$PROJECT_ID" \
        --uniform-bucket-level-access \
        --public-access-prevention \
        --quiet
else
    gcloud storage buckets create "gs://${ARTIFACT_BUCKET}" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --default-storage-class=STANDARD \
        --uniform-bucket-level-access \
        --public-access-prevention \
        --quiet
fi

log "Ensuring the Firestore Native (default) database."
if gcloud firestore databases describe --database='(default)' \
    --project="$PROJECT_ID" --format='value(name)' >/dev/null 2>&1; then
    firestore_location="$(
        gcloud firestore databases describe --database='(default)' \
            --project="$PROJECT_ID" --format='value(locationId)' | tr '[:upper:]' '[:lower:]'
    )"
    firestore_type="$(
        gcloud firestore databases describe --database='(default)' \
            --project="$PROJECT_ID" --format='value(type)'
    )"
    [[ "$firestore_location" == "$REGION" ]] || \
        die "Existing Firestore database is in $firestore_location, expected $REGION."
    [[ "$firestore_type" == "FIRESTORE_NATIVE" ]] || \
        die "The (default) database is not Firestore Native: $firestore_type"
else
    gcloud firestore databases create \
        --database='(default)' \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --type=firestore-native \
        --delete-protection \
        --quiet
fi

queue_flags=(
    "--project=$PROJECT_ID"
    "--location=$REGION"
    --max-concurrent-dispatches=1
    --max-dispatches-per-second=1
    # Leave a retry after the 20-minute analysis lease can expire. With the
    # configured backoff, five attempts can all land inside the active lease.
    --max-attempts=7
    --min-backoff=60s
    --max-backoff=600s
    --max-doublings=3
    --max-retry-duration=14400s
    --log-sampling-ratio=1.0
    --quiet
)
log "Ensuring Cloud Tasks queue $TASK_QUEUE."
if gcloud tasks queues describe "$TASK_QUEUE" \
    --project="$PROJECT_ID" --location="$REGION" --format='value(name)' >/dev/null 2>&1; then
    gcloud tasks queues update "$TASK_QUEUE" "${queue_flags[@]}"
else
    gcloud tasks queues create "$TASK_QUEUE" "${queue_flags[@]}"
fi

add_project_role() {
    local member=$1
    local role=$2
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="$member" \
        --role="$role" \
        --condition=None \
        --quiet >/dev/null
}

add_service_account_role() {
    local service_account=$1
    local member=$2
    local role=$3
    gcloud iam service-accounts add-iam-policy-binding "$service_account" \
        --project="$PROJECT_ID" \
        --member="$member" \
        --role="$role" \
        --condition=None \
        --quiet >/dev/null
}

add_bucket_role() {
    local member=$1
    local role=$2
    gcloud storage buckets add-iam-policy-binding "gs://${ARTIFACT_BUCKET}" \
        --project="$PROJECT_ID" \
        --member="$member" \
        --role="$role" \
        --condition=None \
        --quiet >/dev/null
}

log "Applying least-privilege runtime IAM bindings."
add_project_role "serviceAccount:${WEB_SA}" roles/datastore.user
add_project_role "serviceAccount:${WEB_SA}" roles/cloudtasks.enqueuer
add_project_role "serviceAccount:${WORKER_SA}" roles/datastore.user
add_project_role "serviceAccount:${AGENT_SA}" roles/datastore.user
add_project_role "serviceAccount:${AGENT_SA}" roles/cloudtasks.enqueuer
add_project_role "serviceAccount:${AGENT_SA}" roles/aiplatform.user
add_project_role "serviceAccount:${TASKS_SERVICE_AGENT}" roles/cloudtasks.serviceAgent
add_service_account_role \
    "$TASK_INVOKER_SA" "serviceAccount:${WEB_SA}" roles/iam.serviceAccountUser
add_service_account_role \
    "$TASK_INVOKER_SA" "serviceAccount:${AGENT_SA}" roles/iam.serviceAccountUser
add_service_account_role \
    "$TASK_INVOKER_SA" "serviceAccount:${TASKS_SERVICE_AGENT}" roles/iam.serviceAccountUser
add_bucket_role "serviceAccount:${WORKER_SA}" roles/storage.objectUser
add_bucket_role "serviceAccount:${AGENT_SA}" roles/storage.objectUser

# Cloud Build's default identity changed for newer projects. Query it instead
# of assuming the legacy account; repository-level writer is sufficient.
BUILD_SERVICE_ACCOUNT="$(
    gcloud builds get-default-service-account --project="$PROJECT_ID" 2>/dev/null || true
)"
BUILD_SERVICE_ACCOUNT="${BUILD_SERVICE_ACCOUNT##*/}"
if [[ -n "$BUILD_SERVICE_ACCOUNT" ]]; then
    gcloud artifacts repositories add-iam-policy-binding "$ARTIFACT_REPOSITORY" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --member="serviceAccount:${BUILD_SERVICE_ACCOUNT}" \
        --role=roles/artifactregistry.writer \
        --condition=None \
        --quiet >/dev/null
else
    die "Cloud Build default service account could not be resolved after enabling the API."
fi

secret_has_enabled_version() {
    local secret_id=$1
    [[ -n "$(
        gcloud secrets versions list "$secret_id" \
            --project="$PROJECT_ID" \
            --filter='state=ENABLED' \
            --limit=1 \
            --format='value(name)'
    )" ]]
}

input_was_supplied() {
    local file_variable_name=$1
    local value_variable_name=$2
    [[ -n "${!file_variable_name-}" || -n "${!value_variable_name-}" ]]
}

load_secret_input() {
    local file_variable_name=$1
    local value_variable_name=$2
    local generation_mode=$3
    local file_path="${!file_variable_name-}"
    local direct_value="${!value_variable_name-}"

    [[ -z "$file_path" || -z "$direct_value" ]] || \
        die "Set only one of $file_variable_name or $value_variable_name."

    if [[ -n "$file_path" ]]; then
        [[ -f "$file_path" && -r "$file_path" ]] || \
            die "$file_variable_name does not point to a readable regular file."
        SECRET_INPUT_VALUE="$(<"$file_path")"
    elif [[ -n "$direct_value" ]]; then
        SECRET_INPUT_VALUE="$direct_value"
    elif [[ "$generation_mode" == "random-django" ]]; then
        require_command openssl
        SECRET_INPUT_VALUE="$(openssl rand -hex 48)"
    elif [[ "$generation_mode" == "random-task" ]]; then
        require_command openssl
        SECRET_INPUT_VALUE="$(openssl rand -hex 32)"
    else
        die "A first secret version requires $file_variable_name (preferred) or $value_variable_name."
    fi

    [[ "$SECRET_INPUT_VALUE" != *$'\n'* && "$SECRET_INPUT_VALUE" != *$'\r'* ]] || \
        die "Secret input must be a single line."
}

validate_secret_input() {
    local validation_mode=$1
    local length=${#SECRET_INPUT_VALUE}

    case "$validation_mode" in
        django)
            ((length >= 32)) || die "Django secret must contain at least 32 characters."
            ;;
        password-hash)
            ((length >= 20)) || die "Django password hash is unexpectedly short."
            [[ "$SECRET_INPUT_VALUE" == *'$'* ]] || \
                die "Demo password input must be a Django encoded password hash, not a password."
            ;;
        task)
            ((length >= 32)) || die "Cloud Tasks shared secret must contain at least 32 characters."
            [[ "$SECRET_INPUT_VALUE" =~ ^[[:graph:]]+$ ]] || \
                die "Cloud Tasks shared secret must use printable ASCII without spaces."
            ;;
        provider-key)
            ((length >= 16)) || die "Provider key is unexpectedly short."
            [[ "$SECRET_INPUT_VALUE" =~ ^[[:graph:]]+$ ]] || \
                die "Provider key must use printable ASCII without spaces."
            ;;
        email)
            ((length <= 254)) || die "Contact recipient email is too long."
            [[ "$SECRET_INPUT_VALUE" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || \
                die "Contact recipient must be a single valid email address."
            ;;
        *)
            die "Unknown secret validation mode: $validation_mode"
            ;;
    esac
}

ensure_secret() {
    local secret_id=$1
    local file_variable_name=$2
    local value_variable_name=$3
    local generation_mode=$4
    local validation_mode=$5
    local secret_exists=false
    local has_version=false

    if gcloud secrets describe "$secret_id" \
        --project="$PROJECT_ID" --format='value(name)' >/dev/null 2>&1; then
        secret_exists=true
    else
        log "Creating Secret Manager secret: $secret_id"
        gcloud secrets create "$secret_id" \
            --project="$PROJECT_ID" \
            --replication-policy=automatic \
            --labels='app=agentic-agriculture,managed-by=bootstrap' \
            --quiet
    fi

    if secret_has_enabled_version "$secret_id"; then
        has_version=true
    fi

    if [[ "$has_version" == "true" && "$ROTATE_SECRETS" != "true" ]]; then
        if input_was_supplied "$file_variable_name" "$value_variable_name"; then
            log "Preserving existing version of $secret_id; set AA_ROTATE_SECRETS=true to rotate it."
        else
            log "Secret already has an enabled version: $secret_id"
        fi
        return
    fi

    if [[ "$secret_exists" == "true" && "$has_version" == "false" ]]; then
        log "Secret exists without an enabled version: $secret_id"
    fi

    SECRET_INPUT_VALUE=""
    load_secret_input "$file_variable_name" "$value_variable_name" "$generation_mode"
    validate_secret_input "$validation_mode"
    printf '%s' "$SECRET_INPUT_VALUE" | gcloud secrets versions add "$secret_id" \
        --project="$PROJECT_ID" \
        --data-file=- \
        --quiet >/dev/null
    SECRET_INPUT_VALUE=""
    log "Added a new enabled version to $secret_id."
}

ensure_secret \
    "$DJANGO_SECRET_ID" DJANGO_SECRET_KEY_FILE DJANGO_SECRET_KEY_VALUE random-django django
ensure_secret \
    "$DEMO_PASSWORD_SECRET_ID" DEMO_PASSWORD_HASH_FILE DEMO_PASSWORD_HASH_VALUE required \
    password-hash
ensure_secret \
    "$TASK_SECRET_ID" CLOUD_TASKS_SHARED_SECRET_FILE CLOUD_TASKS_SHARED_SECRET_VALUE \
    random-task task
ensure_secret \
    "$RESEND_SECRET_ID" CONTACT_RESEND_API_KEY_FILE CONTACT_RESEND_API_KEY_VALUE \
    required provider-key
ensure_secret \
    "$TURNSTILE_SECRET_ID" CONTACT_TURNSTILE_SECRET_KEY_FILE CONTACT_TURNSTILE_SECRET_KEY_VALUE \
    required provider-key
ensure_secret \
    "$CONTACT_RECIPIENT_SECRET_ID" CONTACT_TO_EMAIL_FILE CONTACT_TO_EMAIL_VALUE \
    required email

add_secret_role() {
    local secret_id=$1
    local service_account=$2
    gcloud secrets add-iam-policy-binding "$secret_id" \
        --project="$PROJECT_ID" \
        --member="serviceAccount:${service_account}" \
        --role=roles/secretmanager.secretAccessor \
        --condition=None \
        --quiet >/dev/null
}

log "Granting secret access only to the runtime identities that need it."
add_secret_role "$DJANGO_SECRET_ID" "$WEB_SA"
add_secret_role "$DEMO_PASSWORD_SECRET_ID" "$WEB_SA"
add_secret_role "$TASK_SECRET_ID" "$WEB_SA"
add_secret_role "$RESEND_SECRET_ID" "$WEB_SA"
add_secret_role "$TURNSTILE_SECRET_ID" "$WEB_SA"
add_secret_role "$CONTACT_RECIPIENT_SECRET_ID" "$WEB_SA"
add_secret_role "$DJANGO_SECRET_ID" "$WORKER_SA"
add_secret_role "$TASK_SECRET_ID" "$WORKER_SA"
add_secret_role "$DJANGO_SECRET_ID" "$AGENT_SA"
add_secret_role "$TASK_SECRET_ID" "$AGENT_SA"

log "Bootstrap complete. No secret values were printed."
printf '%s\n' \
    "Project: $PROJECT_ID" \
    "Region: $REGION" \
    "Artifact repository: $ARTIFACT_REPOSITORY" \
    "Artifact bucket: gs://$ARTIFACT_BUCKET" \
    "Cloud Tasks queue: $TASK_QUEUE" \
    "Next: bash infra/gcp/deploy.sh"
