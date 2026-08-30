#!/usr/bin/env bash
# Reconcile a Google-managed custom domain in front of the public Cloud Run web service.
#
# This uses the official global external classic Application Load Balancer architecture:
# global IPv4 -> HTTP/HTTPS forwarding rules -> target proxies -> URL map ->
# global backend service -> regional serverless NEG -> Cloud Run.
#
# The script deliberately does not create or modify DNS records. During certificate
# provisioning, point DNS-only A records at the printed global IPv4 address.
#
# Required:
#   export GCP_PROJECT_ID="agentic-agriculture-2026"
#   export AA_CUSTOM_DOMAIN="1415agri.com"
# Optional:
#   export AA_CUSTOM_WWW_DOMAIN="www.1415agri.com"  # defaults to www.$AA_CUSTOM_DOMAIN
#   export AA_CUSTOM_EXPECTED_IP="136.110.164.101" # fail if the reserved IP differs

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

command -v gcloud >/dev/null 2>&1 || die "Required command not found: gcloud"

PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-southamerica-east1}"
WEB_SERVICE="${AA_WEB_SERVICE:-agentic-agriculture-web}"
CUSTOM_DOMAIN="${AA_CUSTOM_DOMAIN:-${1:-}}"
CUSTOM_WWW_DOMAIN="${AA_CUSTOM_WWW_DOMAIN:-${2:-}}"
EXPECTED_IP="${AA_CUSTOM_EXPECTED_IP:-}"

IP_NAME="${AA_CUSTOM_IP_NAME:-aa-web-ip}"
NEG_NAME="${AA_CUSTOM_NEG_NAME:-aa-web-neg}"
BACKEND_NAME="${AA_CUSTOM_BACKEND_NAME:-aa-web-backend}"
URL_MAP_NAME="${AA_CUSTOM_URL_MAP_NAME:-aa-web-url-map}"
CERT_NAME="${AA_CUSTOM_CERT_NAME:-aa-web-cert}"
HTTPS_PROXY_NAME="${AA_CUSTOM_HTTPS_PROXY_NAME:-aa-web-https-proxy}"
HTTPS_RULE_NAME="${AA_CUSTOM_HTTPS_RULE_NAME:-aa-web-https}"
HTTP_PROXY_NAME="${AA_CUSTOM_HTTP_PROXY_NAME:-aa-web-http-proxy}"
HTTP_RULE_NAME="${AA_CUSTOM_HTTP_RULE_NAME:-aa-web-http}"

[[ -n "$PROJECT_ID" ]] || die "Set GCP_PROJECT_ID before running this script."
[[ -n "$CUSTOM_DOMAIN" ]] || \
    die "Set AA_CUSTOM_DOMAIN or pass the apex domain as the first argument."
CUSTOM_WWW_DOMAIN="${CUSTOM_WWW_DOMAIN:-www.${CUSTOM_DOMAIN}}"

validate_hostname() {
    local hostname=$1
    local label
    local -a labels=()

    [[ ${#hostname} -le 253 ]] || return 1
    [[ "$hostname" == "${hostname,,}" ]] || return 1
    [[ "$hostname" == *.* ]] || return 1
    [[ "$hostname" != .* && "$hostname" != *. ]] || return 1
    [[ "$hostname" != *'..'* ]] || return 1
    [[ "$hostname" != *'/'* && "$hostname" != *':'* && "$hostname" != *','* ]] || return 1
    [[ "$hostname" != *'|'* && "$hostname" != *$'\n'* && "$hostname" != *$'\r'* ]] || return 1

    IFS='.' read -r -a labels <<<"$hostname"
    ((${#labels[@]} >= 2)) || return 1
    for label in "${labels[@]}"; do
        [[ ${#label} -le 63 ]] || return 1
        [[ "$label" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || return 1
    done
}

validate_ipv4() {
    local address=$1
    local octet
    local -a octets=()

    [[ "$address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
    IFS='.' read -r -a octets <<<"$address"
    ((${#octets[@]} == 4)) || return 1
    for octet in "${octets[@]}"; do
        [[ "$octet" =~ ^(0|[1-9][0-9]{0,2})$ ]] || return 1
        ((10#$octet <= 255)) || return 1
    done
}

validate_resource_name() {
    local resource_name=$1
    [[ ${#resource_name} -le 63 ]] || return 1
    [[ "$resource_name" =~ ^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]]
}

reference_list_is_exactly() {
    local raw_references=$1
    local expected_suffix=$2
    local normalized="${raw_references//;/,}"
    local -a references=()

    IFS=',' read -r -a references <<<"$normalized"
    ((${#references[@]} == 1)) || return 1
    [[ "${references[0]}" == *"$expected_suffix" ]]
}

certificate_domains_are_exact() {
    local raw_domains=$1
    local normalized="${raw_domains//;/,}"
    local candidate
    local apex_found=false
    local www_found=false
    local -a domains=()

    IFS=',' read -r -a domains <<<"$normalized"
    ((${#domains[@]} == 2)) || return 1
    for candidate in "${domains[@]}"; do
        [[ "$candidate" == "$CUSTOM_DOMAIN" ]] && apex_found=true
        [[ "$candidate" == "$CUSTOM_WWW_DOMAIN" ]] && www_found=true
    done
    [[ "$apex_found" == "true" && "$www_found" == "true" ]]
}

[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || die "GCP_PROJECT_ID is invalid."
[[ "$REGION" =~ ^[a-z]+[a-z0-9-]*[0-9]$ ]] || die "GCP_REGION is invalid."
[[ "$WEB_SERVICE" =~ ^[a-z]([a-z0-9-]{0,47}[a-z0-9])?$ ]] || \
    die "AA_WEB_SERVICE is invalid."
validate_hostname "$CUSTOM_DOMAIN" || die "AA_CUSTOM_DOMAIN is not a canonical hostname."
validate_hostname "$CUSTOM_WWW_DOMAIN" || \
    die "AA_CUSTOM_WWW_DOMAIN is not a canonical hostname."
[[ "$CUSTOM_DOMAIN" != "$CUSTOM_WWW_DOMAIN" ]] || die "The apex and www hostnames must differ."
if [[ -n "$EXPECTED_IP" ]]; then
    validate_ipv4 "$EXPECTED_IP" || die "AA_CUSTOM_EXPECTED_IP is not a valid IPv4 address."
fi

for resource_name in \
    "$IP_NAME" "$NEG_NAME" "$BACKEND_NAME" "$URL_MAP_NAME" "$CERT_NAME" \
    "$HTTPS_PROXY_NAME" "$HTTPS_RULE_NAME" "$HTTP_PROXY_NAME" "$HTTP_RULE_NAME"; do
    validate_resource_name "$resource_name" || die "Invalid Google Cloud resource name: $resource_name"
done

ACTIVE_ACCOUNT="$(gcloud auth list --filter='status:ACTIVE' --format='value(account)' | head -n 1)"
[[ -n "$ACTIVE_ACCOUNT" ]] || die "No active gcloud account. Run: gcloud auth login"
gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null
gcloud services enable compute.googleapis.com --project="$PROJECT_ID" --quiet
gcloud run services describe "$WEB_SERVICE" \
    --project="$PROJECT_ID" --region="$REGION" --format='value(metadata.name)' >/dev/null || \
    die "Cloud Run web service $WEB_SERVICE does not exist in $REGION. Run deploy.sh first."

log "Reconciling global IPv4 address $IP_NAME."
if gcloud compute addresses describe "$IP_NAME" \
    --project="$PROJECT_ID" --global --format='value(name)' >/dev/null 2>&1; then
    address_type="$(
        gcloud compute addresses describe "$IP_NAME" \
            --project="$PROJECT_ID" --global --format='value(addressType)'
    )"
    ip_version="$(
        gcloud compute addresses describe "$IP_NAME" \
            --project="$PROJECT_ID" --global --format='value(ipVersion)'
    )"
    [[ "$address_type" == "EXTERNAL" && "$ip_version" == "IPV4" ]] || \
        die "$IP_NAME exists but is not a global external IPv4 address."
else
    gcloud compute addresses create "$IP_NAME" \
        --project="$PROJECT_ID" --global --ip-version=IPV4 --network-tier=PREMIUM --quiet
fi
GLOBAL_IP="$(
    gcloud compute addresses describe "$IP_NAME" \
        --project="$PROJECT_ID" --global --format='value(address)'
)"
validate_ipv4 "$GLOBAL_IP" || die "Google Cloud returned an invalid global IPv4 address."
if [[ -n "$EXPECTED_IP" && "$GLOBAL_IP" != "$EXPECTED_IP" ]]; then
    die "Reserved IP is $GLOBAL_IP, but AA_CUSTOM_EXPECTED_IP requires $EXPECTED_IP."
fi

log "Reconciling regional serverless NEG $NEG_NAME."
if gcloud compute network-endpoint-groups describe "$NEG_NAME" \
    --project="$PROJECT_ID" --region="$REGION" --format='value(name)' >/dev/null 2>&1; then
    neg_type="$(
        gcloud compute network-endpoint-groups describe "$NEG_NAME" \
            --project="$PROJECT_ID" --region="$REGION" --format='value(networkEndpointType)'
    )"
    neg_service="$(
        gcloud compute network-endpoint-groups describe "$NEG_NAME" \
            --project="$PROJECT_ID" --region="$REGION" --format='value(cloudRun.service)'
    )"
    [[ "$neg_type" == "SERVERLESS" && "$neg_service" == "$WEB_SERVICE" ]] || \
        die "$NEG_NAME exists but does not target Cloud Run service $WEB_SERVICE."
else
    gcloud compute network-endpoint-groups create "$NEG_NAME" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --network-endpoint-type=serverless \
        --cloud-run-service="$WEB_SERVICE" \
        --quiet
fi

log "Reconciling global external backend service $BACKEND_NAME."
if gcloud compute backend-services describe "$BACKEND_NAME" \
    --project="$PROJECT_ID" --global --format='value(name)' >/dev/null 2>&1; then
    backend_scheme="$(
        gcloud compute backend-services describe "$BACKEND_NAME" \
            --project="$PROJECT_ID" --global --format='value(loadBalancingScheme)'
    )"
    backend_protocol="$(
        gcloud compute backend-services describe "$BACKEND_NAME" \
            --project="$PROJECT_ID" --global --format='value(protocol)'
    )"
    [[ "$backend_scheme" == "EXTERNAL" && "$backend_protocol" == "HTTP" ]] || \
        die "$BACKEND_NAME exists with an incompatible scheme or protocol."
else
    gcloud compute backend-services create "$BACKEND_NAME" \
        --project="$PROJECT_ID" \
        --global \
        --load-balancing-scheme=EXTERNAL \
        --protocol=HTTP \
        --quiet
fi

backend_groups="$(
    gcloud compute backend-services describe "$BACKEND_NAME" \
        --project="$PROJECT_ID" --global --format='value(backends.group)'
)"
if [[ -z "$backend_groups" ]]; then
    gcloud compute backend-services add-backend "$BACKEND_NAME" \
        --project="$PROJECT_ID" \
        --global \
        --network-endpoint-group="$NEG_NAME" \
        --network-endpoint-group-region="$REGION" \
        --quiet
elif ! reference_list_is_exactly \
    "$backend_groups" "/regions/${REGION}/networkEndpointGroups/${NEG_NAME}"; then
    die "$BACKEND_NAME already has a different backend; refusing to replace it."
fi

log "Reconciling global URL map $URL_MAP_NAME."
if gcloud compute url-maps describe "$URL_MAP_NAME" \
    --project="$PROJECT_ID" --global --format='value(name)' >/dev/null 2>&1; then
    default_service="$(
        gcloud compute url-maps describe "$URL_MAP_NAME" \
            --project="$PROJECT_ID" --global --format='value(defaultService)'
    )"
    [[ "$default_service" == *"/global/backendServices/${BACKEND_NAME}" ]] || \
        die "$URL_MAP_NAME points to another backend; refusing to replace it."
else
    gcloud compute url-maps create "$URL_MAP_NAME" \
        --project="$PROJECT_ID" --global --default-service="$BACKEND_NAME" --quiet
fi

log "Reconciling Google-managed certificate $CERT_NAME."
if gcloud compute ssl-certificates describe "$CERT_NAME" \
    --project="$PROJECT_ID" --global --format='value(name)' >/dev/null 2>&1; then
    cert_type="$(
        gcloud compute ssl-certificates describe "$CERT_NAME" \
            --project="$PROJECT_ID" --global --format='value(type)'
    )"
    cert_domains="$(
        gcloud compute ssl-certificates describe "$CERT_NAME" \
            --project="$PROJECT_ID" --global --format='value(managed.domains)'
    )"
    [[ "$cert_type" == "MANAGED" ]] || die "$CERT_NAME is not a Google-managed certificate."
    certificate_domains_are_exact "$cert_domains" || \
        die "$CERT_NAME exists for different hostnames; refusing destructive certificate replacement."
else
    gcloud compute ssl-certificates create "$CERT_NAME" \
        --project="$PROJECT_ID" \
        --global \
        --domains="${CUSTOM_DOMAIN},${CUSTOM_WWW_DOMAIN}" \
        --quiet
fi

log "Reconciling HTTPS target proxy $HTTPS_PROXY_NAME."
if gcloud compute target-https-proxies describe "$HTTPS_PROXY_NAME" \
    --project="$PROJECT_ID" --global --format='value(name)' >/dev/null 2>&1; then
    https_url_map="$(
        gcloud compute target-https-proxies describe "$HTTPS_PROXY_NAME" \
            --project="$PROJECT_ID" --global --format='value(urlMap)'
    )"
    https_certificates="$(
        gcloud compute target-https-proxies describe "$HTTPS_PROXY_NAME" \
            --project="$PROJECT_ID" --global --format='value(sslCertificates)'
    )"
    [[ "$https_url_map" == *"/global/urlMaps/${URL_MAP_NAME}" ]] || \
        die "$HTTPS_PROXY_NAME uses another URL map; refusing to replace it."
    reference_list_is_exactly "$https_certificates" "/global/sslCertificates/${CERT_NAME}" || \
        die "$HTTPS_PROXY_NAME uses another certificate; refusing to replace it."
else
    gcloud compute target-https-proxies create "$HTTPS_PROXY_NAME" \
        --project="$PROJECT_ID" \
        --global \
        --url-map="$URL_MAP_NAME" \
        --ssl-certificates="$CERT_NAME" \
        --quiet
fi

log "Reconciling HTTP target proxy $HTTP_PROXY_NAME."
if gcloud compute target-http-proxies describe "$HTTP_PROXY_NAME" \
    --project="$PROJECT_ID" --global --format='value(name)' >/dev/null 2>&1; then
    http_url_map="$(
        gcloud compute target-http-proxies describe "$HTTP_PROXY_NAME" \
            --project="$PROJECT_ID" --global --format='value(urlMap)'
    )"
    [[ "$http_url_map" == *"/global/urlMaps/${URL_MAP_NAME}" ]] || \
        die "$HTTP_PROXY_NAME uses another URL map; refusing to replace it."
else
    gcloud compute target-http-proxies create "$HTTP_PROXY_NAME" \
        --project="$PROJECT_ID" --global --url-map="$URL_MAP_NAME" --quiet
fi

ensure_forwarding_rule() {
    local rule_name=$1
    local port=$2
    local proxy_type=$3
    local proxy_name=$4
    local target_flag
    local rule_ip
    local rule_port
    local rule_scheme
    local rule_target
    local target_collection

    if [[ "$proxy_type" == "https" ]]; then
        target_flag="--target-https-proxy=${proxy_name}"
        target_collection="targetHttpsProxies"
    else
        target_flag="--target-http-proxy=${proxy_name}"
        target_collection="targetHttpProxies"
    fi

    if gcloud compute forwarding-rules describe "$rule_name" \
        --project="$PROJECT_ID" --global --format='value(name)' >/dev/null 2>&1; then
        rule_ip="$(
            gcloud compute forwarding-rules describe "$rule_name" \
                --project="$PROJECT_ID" --global --format='value(IPAddress)'
        )"
        rule_port="$(
            gcloud compute forwarding-rules describe "$rule_name" \
                --project="$PROJECT_ID" --global --format='value(portRange)'
        )"
        rule_scheme="$(
            gcloud compute forwarding-rules describe "$rule_name" \
                --project="$PROJECT_ID" --global --format='value(loadBalancingScheme)'
        )"
        rule_target="$(
            gcloud compute forwarding-rules describe "$rule_name" \
                --project="$PROJECT_ID" --global --format='value(target)'
        )"
        [[ "$rule_ip" == "$GLOBAL_IP" && "$rule_scheme" == "EXTERNAL" ]] || \
            die "$rule_name uses an incompatible address or load-balancing scheme."
        [[ "$rule_port" == "$port" || "$rule_port" == "${port}-${port}" ]] || \
            die "$rule_name uses port range $rule_port instead of $port."
        [[ "$rule_target" == *"/global/${target_collection}/${proxy_name}" ]] || \
            die "$rule_name targets another proxy; refusing to replace it."
        return
    fi

    gcloud compute forwarding-rules create "$rule_name" \
        --project="$PROJECT_ID" \
        --global \
        --load-balancing-scheme=EXTERNAL \
        --network-tier=PREMIUM \
        --address="$IP_NAME" \
        --ports="$port" \
        "$target_flag" \
        --quiet
}

log "Reconciling global HTTPS and HTTP forwarding rules."
ensure_forwarding_rule "$HTTPS_RULE_NAME" 443 https "$HTTPS_PROXY_NAME"
ensure_forwarding_rule "$HTTP_RULE_NAME" 80 http "$HTTP_PROXY_NAME"

CERT_STATUS="$(
    gcloud compute ssl-certificates describe "$CERT_NAME" \
        --project="$PROJECT_ID" --global --format='value(managed.status)'
)"
DOMAIN_STATUS="$(
    gcloud compute ssl-certificates describe "$CERT_NAME" \
        --project="$PROJECT_ID" --global --format='yaml(managed.domainStatus)'
)"

log "Custom-domain load balancer is reconciled; DNS was not modified."
printf '%s\n' \
    "Expected DNS records (Cloudflare DNS-only while the certificate provisions):" \
    "A  ${CUSTOM_DOMAIN}      ${GLOBAL_IP}" \
    "A  ${CUSTOM_WWW_DOMAIN}  ${GLOBAL_IP}" \
    "Managed certificate: ${CERT_NAME}" \
    "Certificate status: ${CERT_STATUS}" \
    "Domain status:"
printf '%s\n' "$DOMAIN_STATUS"
if [[ "$CERT_STATUS" != "ACTIVE" ]]; then
    printf '%s\n' \
        "Certificate issuance is asynchronous. Keep both A records DNS-only and rerun this script to check status."
fi
