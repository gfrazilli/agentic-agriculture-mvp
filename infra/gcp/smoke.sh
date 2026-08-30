#!/usr/bin/env bash
# Verify the deployed resources, IAM boundary, and HTTP entry points.
# The active gcloud identity must be allowed to invoke the private services.

set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export LC_ALL=C

readonly SCRIPT_NAME="${0##*/}"
TEMP_DIR=""

log() {
    printf '[%s] %s\n' "$SCRIPT_NAME" "$*"
}

die() {
    printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2
    exit 1
}

cleanup() {
    if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
}
trap cleanup EXIT

for command_name in gcloud curl python3 mktemp; do
    command -v "$command_name" >/dev/null 2>&1 || die "Required command not found: $command_name"
done

PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-southamerica-east1}"
ARTIFACT_BUCKET="${AA_ARTIFACT_BUCKET:-${PROJECT_ID}-agentic-agriculture-artifacts}"
TASK_QUEUE="${AA_TASK_QUEUE:-sentinel-analysis}"
WEB_SERVICE="${AA_WEB_SERVICE:-agentic-agriculture-web}"
WORKER_SERVICE="${AA_WORKER_SERVICE:-agentic-agriculture-worker}"
MCP_SERVICE="${AA_MCP_SERVICE:-agentic-agriculture-mcp}"
AGENT_SERVICE="${AA_AGENT_SERVICE:-agentic-agriculture-agent}"

WEB_SA="${AA_WEB_SERVICE_ACCOUNT_ID:-aa-web}@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="${AA_WORKER_SERVICE_ACCOUNT_ID:-aa-worker}@${PROJECT_ID}.iam.gserviceaccount.com"
MCP_SA="${AA_MCP_SERVICE_ACCOUNT_ID:-aa-mcp}@${PROJECT_ID}.iam.gserviceaccount.com"
AGENT_SA="${AA_AGENT_SERVICE_ACCOUNT_ID:-aa-agent}@${PROJECT_ID}.iam.gserviceaccount.com"

[[ -n "$PROJECT_ID" ]] || die "Set GCP_PROJECT_ID before running this script."
TEMP_DIR="$(mktemp -d)"

service_url() {
    gcloud run services describe "$1" \
        --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)'
}

assert_json_status() {
    local file_path=$1
    local expected=$2
    python3 - "$file_path" "$expected" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
if payload.get("status") != sys.argv[2]:
    raise SystemExit(f"unexpected status payload: {payload!r}")
if sys.argv[2] == "ready" and not all(payload.get("checks", {}).values()):
    raise SystemExit(f"one or more readiness checks failed: {payload!r}")
PY
}

service_is_public() {
    local service_name=$1
    local service_path="$TEMP_DIR/${service_name}-service.json"
    local policy_path="$TEMP_DIR/${service_name}-policy.json"
    gcloud run services describe "$service_name" \
        --project="$PROJECT_ID" --region="$REGION" --format=json >"$service_path"
    gcloud run services get-iam-policy "$service_name" \
        --project="$PROJECT_ID" --region="$REGION" --format=json >"$policy_path"
    python3 - "$service_path" "$policy_path" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    service = json.load(source)
with open(sys.argv[2], encoding="utf-8") as source:
    policy = json.load(source)
annotations = service.get("metadata", {}).get("annotations", {})
invoker_check_disabled = (
    str(annotations.get("run.googleapis.com/invoker-iam-disabled", "")).lower()
    == "true"
)
public_members = {"allUsers", "allAuthenticatedUsers"}
public_binding = any(
    binding.get("role") == "roles/run.invoker"
    and public_members.intersection(binding.get("members", []))
    for binding in policy.get("bindings", [])
)
raise SystemExit(0 if invoker_check_disabled or public_binding else 1)
PY
}

assert_service_shape() {
    local service_name=$1
    local expected_account=$2
    local expected_max=$3
    local expected_concurrency=$4
    local expected_timeout=$5
    local output_path="$TEMP_DIR/${service_name}.json"

    gcloud run services describe "$service_name" \
        --project="$PROJECT_ID" --region="$REGION" --format=json >"$output_path"
    python3 - "$output_path" "$expected_account" "$expected_max" \
        "$expected_concurrency" "$expected_timeout" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    service = json.load(source)
expected_account, expected_max, expected_concurrency, expected_timeout = sys.argv[2:]
template = service.get("spec", {}).get("template", {})
spec = template.get("spec", {})
annotations = template.get("metadata", {}).get("annotations", {})
account = spec.get("serviceAccountName", "")
max_scale = annotations.get("autoscaling.knative.dev/maxScale")
if max_scale is None:
    max_scale = template.get("scaling", {}).get("maxInstanceCount")
concurrency = spec.get("containerConcurrency")
timeout = str(spec.get("timeoutSeconds", "")).removesuffix("s")
if account != expected_account:
    raise SystemExit(f"unexpected service account: {account!r}")
if str(max_scale) != expected_max:
    raise SystemExit(f"unexpected max instances: {max_scale!r}")
if str(concurrency) != expected_concurrency:
    raise SystemExit(f"unexpected concurrency: {concurrency!r}")
if timeout != expected_timeout:
    raise SystemExit(f"unexpected timeout: {timeout!r}")
containers = spec.get("containers", [])
if not containers or not containers[0].get("image"):
    raise SystemExit("service has no deployed image")
print(containers[0]["image"])
PY
}

WEB_URL="$(service_url "$WEB_SERVICE")"
WORKER_URL="$(service_url "$WORKER_SERVICE")"
MCP_URL="$(service_url "$MCP_SERVICE")"
AGENT_URL="$(service_url "$AGENT_SERVICE")"
for url in "$WEB_URL" "$WORKER_URL" "$MCP_URL" "$AGENT_URL"; do
    [[ "$url" == https://*.run.app ]] || die "Unexpected Cloud Run URL: $url"
done

log "Checking service identities, scaling, concurrency, timeout, and shared image."
WEB_IMAGE="$(assert_service_shape "$WEB_SERVICE" "$WEB_SA" 2 20 300)"
WORKER_IMAGE="$(assert_service_shape "$WORKER_SERVICE" "$WORKER_SA" 1 1 900)"
MCP_IMAGE="$(assert_service_shape "$MCP_SERVICE" "$MCP_SA" 2 20 60)"
AGENT_IMAGE="$(assert_service_shape "$AGENT_SERVICE" "$AGENT_SA" 1 4 300)"
[[ "$WEB_IMAGE" == "$WORKER_IMAGE" && "$WEB_IMAGE" == "$MCP_IMAGE" && \
    "$WEB_IMAGE" == "$AGENT_IMAGE" ]] || die "Cloud Run roles do not use the same image."

log "Checking the public/private Cloud Run IAM boundary."
service_is_public "$WEB_SERVICE" || die "Web service is not publicly invokable."
for private_service in "$WORKER_SERVICE" "$MCP_SERVICE" "$AGENT_SERVICE"; do
    if service_is_public "$private_service"; then
        die "Private service has a public invoker binding: $private_service"
    fi
done

for private_url in "$WORKER_URL/login/" "$MCP_URL/mcp" "$AGENT_URL/list-apps"; do
    status_code="$(curl --silent --output /dev/null --write-out '%{http_code}' "$private_url")"
    # Cloud Run may deliberately hide a private route with 404 instead of
    # exposing whether the service exists. IAM policy was checked above.
    [[ "$status_code" == "401" || "$status_code" == "403" || "$status_code" == "404" ]] || \
        die "Unauthenticated private request returned HTTP $status_code: $private_url"
done

log "Checking Firestore, Cloud Storage, and Cloud Tasks configuration."
firestore_location="$(
    gcloud firestore databases describe --database='(default)' \
        --project="$PROJECT_ID" --format='value(locationId)'
)"
firestore_type="$(
    gcloud firestore databases describe --database='(default)' \
        --project="$PROJECT_ID" --format='value(type)'
)"
[[ "${firestore_location,,}" == "$REGION" && "$firestore_type" == "FIRESTORE_NATIVE" ]] || \
    die "Firestore database location or type is incorrect."

queue_state="$(
    gcloud tasks queues describe "$TASK_QUEUE" \
        --project="$PROJECT_ID" --location="$REGION" --format='value(state)'
)"
[[ "$queue_state" == "RUNNING" ]] || die "Cloud Tasks queue is not RUNNING: $queue_state"

gcloud storage buckets describe "gs://${ARTIFACT_BUCKET}" \
    --project="$PROJECT_ID" --format=json >"$TEMP_DIR/bucket.json"
python3 - "$TEMP_DIR/bucket.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    bucket = json.load(source)
iam = bucket.get("iamConfiguration", {})
uniform = bucket.get("uniform_bucket_level_access")
if uniform is None:
    uniform = iam.get("uniformBucketLevelAccess", {}).get("enabled")
prevention = str(
    bucket.get("public_access_prevention", iam.get("publicAccessPrevention", ""))
).lower()
if uniform is not True or prevention != "enforced":
    raise SystemExit("bucket must enforce uniform access and public access prevention")
PY

log "Checking the public login surface and readiness."
login_status="$(curl --silent --output /dev/null --write-out '%{http_code}' "$WEB_URL/login/")"
[[ "$login_status" == "200" ]] || die "Public login returned HTTP $login_status."
curl --fail --silent --show-error "$WEB_URL/ready" >"$TEMP_DIR/ready.json"
assert_json_status "$TEMP_DIR/ready.json" ready

if [[ "${AA_SKIP_AUTHENTICATED_SMOKE:-false}" != "true" ]]; then
    log "Checking authenticated worker, MCP, and ADK endpoints."
    IDENTITY_TOKEN="${AA_IDENTITY_TOKEN:-$(gcloud auth print-identity-token)}"
    [[ -n "$IDENTITY_TOKEN" ]] || die "Could not obtain a Google identity token."

    curl --fail --silent --show-error \
        --header="Authorization: Bearer ${IDENTITY_TOKEN}" \
        "$WORKER_URL/login/" >"$TEMP_DIR/worker-login.html" || \
        die "The active identity needs roles/run.invoker on the private worker for smoke testing."

    curl --fail --silent --show-error \
        --header="Authorization: Bearer ${IDENTITY_TOKEN}" \
        "$AGENT_URL/list-apps" >"$TEMP_DIR/apps.json" || \
        die "The active identity needs roles/run.invoker on the private agent for smoke testing."
    python3 - "$TEMP_DIR/apps.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    apps = json.load(source)
if "agentic_agriculture" not in apps:
    raise SystemExit(f"ADK application not found: {apps!r}")
PY

    curl --fail --silent --show-error \
        --request POST \
        --header="Authorization: Bearer ${IDENTITY_TOKEN}" \
        --header='Content-Type: application/json' \
        --header='Accept: application/json, text/event-stream' \
        --data='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"agentic-agriculture-smoke","version":"1.0"}}}' \
        "$MCP_URL/mcp" >"$TEMP_DIR/mcp.json" || \
        die "The active identity needs roles/run.invoker on the private MCP for smoke testing."
    python3 - "$TEMP_DIR/mcp.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
if not payload.get("result", {}).get("serverInfo"):
    raise SystemExit(f"MCP initialization failed: {payload!r}")
PY
    IDENTITY_TOKEN=""
else
    log "Skipping authenticated endpoint checks because AA_SKIP_AUTHENTICATED_SMOKE=true."
fi

log "Smoke checks passed."
printf '%s\n' \
    "Web: $WEB_URL" \
    "Worker: private" \
    "MCP: private" \
    "Agent: private" \
    "Image: $WEB_IMAGE"
