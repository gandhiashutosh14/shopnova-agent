#!/usr/bin/env bash
# Deploy the four tools as Cloud Functions (2nd gen) and the orchestrator to Cloud Run.
#
# Prerequisites: gcloud CLI authenticated, billing enabled, and these APIs on:
#   gcloud services enable cloudfunctions.googleapis.com run.googleapis.com \
#       cloudbuild.googleapis.com firestore.googleapis.com pubsub.googleapis.com
#
# Usage: PROJECT_ID=my-proj REGION=us-central1 GEMINI_API_KEY=... ./scripts/deploy.sh
#
# Status: this script encodes the intended deployment. It has not been executed from this
# repository (see docs/DEVELOPMENT_NOTES.md), so treat it as a starting point, not a guarantee.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${GEMINI_API_KEY:?set GEMINI_API_KEY}"
REGION="${REGION:-us-central1}"
ESCALATION_TOPIC="${ESCALATION_TOPIC:-shopnova-escalations}"

gcloud config set project "$PROJECT_ID"

# 1. Escalation topic (idempotent)
gcloud pubsub topics describe "$ESCALATION_TOPIC" >/dev/null 2>&1 \
  || gcloud pubsub topics create "$ESCALATION_TOPIC"

# 2. Tools → Cloud Functions. Function names are the kebab-case form the orchestrator expects.
for fn in get_order_status initiate_return issue_refund search_knowledge_base; do
  gcloud functions deploy "${fn//_/-}" \
    --gen2 --runtime python311 --region "$REGION" \
    --source "functions/$fn" --entry-point "$fn" \
    --trigger-http --allow-unauthenticated
done

# 3. Orchestrator → Cloud Run (built from source with buildpacks; gunicorn is in requirements).
gcloud run deploy shopnova-agent \
  --source orchestrator --region "$REGION" --allow-unauthenticated \
  --set-env-vars "PROJECT_ID=$PROJECT_ID,REGION=$REGION,ESCALATION_TOPIC=$ESCALATION_TOPIC,GEMINI_API_KEY=$GEMINI_API_KEY"

echo "Done. Try: curl -X POST \$(gcloud run services describe shopnova-agent --region $REGION --format 'value(status.url)')/chat -H 'Content-Type: application/json' -d '{\"message\":\"Where is ORD-001?\"}'"
