#!/usr/bin/env bash
# scripts/canary_deploy.sh -- weighted-traffic canary rollout with
# automated rollback for the `api` Container App, run by every
# deploy-* job in .github/workflows/ai-release.yml immediately after
# `azd deploy` creates a new revision. See
# docs/adr/0003-deployment-reliability-and-observability.md for why this
# exists and how it was verified.
#
# `azd deploy` already pushed the new image and created a new revision
# by the time this script runs -- but infra/resources.bicep's
# `activeRevisionsMode: 'Multiple'` plus its static
# `{ latestRevision: true, weight: 100 }` traffic rule means the new
# revision ALREADY has 100% traffic the instant it becomes healthy,
# before this script gets a chance to canary it. That's a known,
# accepted gap (see the ADR's Consequences section) -- Bicep's
# declarative traffic model can't express "0% until an external script
# verifies it," only imperative `az containerapp ingress traffic set`
# calls after the fact can, which is exactly what this script does: it
# race-narrows the exposure window by shifting traffic down to a small
# canary slice as fast as possible after deploy, verifies, then either
# promotes to 100% or rolls back to the previous revision.
#
# Usage: canary_deploy.sh <resource-group> <app-name> [canary-weight] [soak-seconds]
#   canary-weight default: 50   soak-seconds default: 30
set -euo pipefail

RESOURCE_GROUP="${1:?resource group required}"
APP_NAME="${2:?container app name required}"
CANARY_WEIGHT="${3:-50}"
SOAK_SECONDS="${4:-30}"
STABLE_WEIGHT=$((100 - CANARY_WEIGHT))

echo "== Discovering revisions for $APP_NAME in $RESOURCE_GROUP =="
# Active revisions, newest first. The newest is the one `azd deploy` just
# created; if a second one exists, it's the previously-stable revision
# this script can roll back to. `properties.active`/`properties.createdTime`
# are confirmed field names (Azure Container Apps revisions REST schema);
# this exact JMESPath composition of them is not itself a documented
# Microsoft example -- verified independently against a real environment
# before relying on it unattended.
mapfile -t REVISIONS < <(az containerapp revision list \
  --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" \
  --query "reverse(sort_by([?properties.active], &properties.createdTime))[].name" \
  -o tsv)

NEW_REVISION="${REVISIONS[0]}"
echo "New revision: $NEW_REVISION"

NEW_FQDN=$(az containerapp revision show \
  --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" --revision "$NEW_REVISION" \
  --query "properties.fqdn" -o tsv)

if [ "${#REVISIONS[@]}" -lt 2 ]; then
  echo "== First-ever deploy for this environment -- no previous revision to canary against or roll back to =="
  echo "== Verifying the new revision directly (already at 100% traffic -- nothing to gate) =="
  python scripts/verify_deployment.py --url "https://${NEW_FQDN}"
  exit $?
fi

STABLE_REVISION="${REVISIONS[1]}"
echo "Previous stable revision: $STABLE_REVISION"

echo "== Shifting traffic: ${CANARY_WEIGHT}% -> $NEW_REVISION, ${STABLE_WEIGHT}% -> $STABLE_REVISION =="
az containerapp ingress traffic set \
  --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" \
  --revision-weight "${NEW_REVISION}=${CANARY_WEIGHT}" "${STABLE_REVISION}=${STABLE_WEIGHT}"

echo "== Verifying the new revision directly (its own FQDN, not the split-traffic main domain) =="
if ! python scripts/verify_deployment.py --url "https://${NEW_FQDN}"; then
  echo "== FAILED -- rolling back: 100% traffic to $STABLE_REVISION, deactivating $NEW_REVISION =="
  az containerapp ingress traffic set \
    --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" \
    --revision-weight "${STABLE_REVISION}=100"
  az containerapp revision deactivate \
    --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" --revision "$NEW_REVISION" || true
  echo "Rolled back to $STABLE_REVISION -- see the verify_deployment.py output above for what failed."
  exit 1
fi

echo "== Canary healthy -- soaking ${SOAK_SECONDS}s at ${CANARY_WEIGHT}% before full promotion =="
sleep "$SOAK_SECONDS"

echo "== Promoting $NEW_REVISION to 100%, deactivating $STABLE_REVISION (cost hygiene -- scale-to-zero old revision) =="
az containerapp ingress traffic set \
  --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" \
  --revision-weight "${NEW_REVISION}=100"
az containerapp revision deactivate \
  --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" --revision "$STABLE_REVISION" || true

echo "== Canary rollout complete: $NEW_REVISION is live at 100% =="
