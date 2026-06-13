#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${SEGMENTS_BUCKET:?Set SEGMENTS_BUCKET}"
: "${INGEST_API_KEY:?Set INGEST_API_KEY}"
REGION="${REGION:-europe-west1}"
SERVICE_NAME="${SERVICE_NAME:-surveillance-ingest}"

gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars "SEGMENTS_BUCKET=${SEGMENTS_BUCKET},INGEST_API_KEY=${INGEST_API_KEY}"
