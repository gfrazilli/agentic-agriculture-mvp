#!/usr/bin/env bash
# Create or update a monthly billing alert scoped to this project.
# A standard budget sends alerts; it does not stop services automatically.

set -Eeuo pipefail
IFS=$'\n\t'
export LC_ALL=C

readonly SCRIPT_NAME="${0##*/}"

log() {
    printf '[%s] %s\n' "$SCRIPT_NAME" "$*"
}

die() {
    printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2
    exit 1
}

command -v gcloud >/dev/null 2>&1 || die "Required command not found: gcloud"

PROJECT_ID="${GCP_PROJECT_ID:-}"
BUDGET_AMOUNT="${AA_MONTHLY_BUDGET_AMOUNT:-100}"
BUDGET_DISPLAY_NAME="${AA_BUDGET_DISPLAY_NAME:-Agentic Agriculture MVP}"

[[ -n "$PROJECT_ID" ]] || die "Set GCP_PROJECT_ID before running this script."
[[ "$BUDGET_AMOUNT" =~ ^[0-9]+([.][0-9]{1,2})?$ ]] || \
    die "AA_MONTHLY_BUDGET_AMOUNT must be a positive decimal amount."
[[ "$BUDGET_AMOUNT" != "0" && "$BUDGET_AMOUNT" != "0.0" && \
    "$BUDGET_AMOUNT" != "0.00" ]] || die "Budget amount must be greater than zero."
[[ ${#BUDGET_DISPLAY_NAME} -le 60 ]] || die "Budget display name is too long."

BILLING_ACCOUNT="${AA_BILLING_ACCOUNT:-$(
    gcloud billing projects describe "$PROJECT_ID" \
        --format='value(billingAccountName)'
)}"
BILLING_ACCOUNT="${BILLING_ACCOUNT#billingAccounts/}"
[[ -n "$BILLING_ACCOUNT" ]] || die "Project has no linked billing account."

gcloud services enable billingbudgets.googleapis.com \
    --project="$PROJECT_ID" \
    --quiet

existing_budget="$(
    gcloud billing budgets list \
        --billing-account="$BILLING_ACCOUNT" \
        --filter="displayName='${BUDGET_DISPLAY_NAME}'" \
        --limit=1 \
        --format='value(name)'
)"
existing_budget="${existing_budget##*/}"

budget_flags=(
    "--billing-account=$BILLING_ACCOUNT"
    "--display-name=$BUDGET_DISPLAY_NAME"
    "--budget-amount=$BUDGET_AMOUNT"
    "--filter-projects=projects/$PROJECT_ID"
    --calendar-period=month
    --threshold-rule=percent=0.50
    --threshold-rule=percent=0.80
    --threshold-rule=percent=1.00
    --threshold-rule=percent=1.00,basis=forecasted-spend
    --quiet
)

if [[ -n "$existing_budget" ]]; then
    log "Updating monthly budget alert: $BUDGET_DISPLAY_NAME"
    gcloud billing budgets update "$existing_budget" "${budget_flags[@]}"
else
    log "Creating monthly budget alert: $BUDGET_DISPLAY_NAME"
    gcloud billing budgets create "${budget_flags[@]}"
fi

log "Budget alert configured for $BUDGET_AMOUNT in the billing account currency."
log "This alert does not automatically cap spending; Cloud Run services still use min=0 and bounded max instances."
