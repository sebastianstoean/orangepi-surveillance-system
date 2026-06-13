#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${SEGMENTS_BUCKET:?Set SEGMENTS_BUCKET}"
: "${ENCRYPT_KEY:?Set ENCRYPT_KEY}"
: "${STATUS_SERVICE_URL:?Set STATUS_SERVICE_URL}"
REGION="${REGION:-europe-west1}"
SERVICE_NAME="${SERVICE_NAME:-surveillance-viewer}"

gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --allow-unauthenticated \
  --ingress internal-and-cloud-load-balancing \
  --set-env-vars "SEGMENTS_BUCKET=${SEGMENTS_BUCKET},ENCRYPT_KEY=${ENCRYPT_KEY},STATUS_SERVICE_URL=${STATUS_SERVICE_URL}"
