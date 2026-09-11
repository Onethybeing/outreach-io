#!/usr/bin/env bash
# Build the dashboard with Cloud Build, deploy it to Cloud Run, and point the API's Google sign-in at it.
#
#   bash scripts/deploy_dashboard.sh
#
# Deploy the API first (scripts/deploy.sh): the dashboard proxies to it, and its URL is baked in at build time.
# Afterwards, add <dashboard URL>/auth/callback as a redirect URI on the Google sign-in OAuth client.
set -euo pipefail

PROJECT="${PROJECT:-outreach-io-sj26}"
REGION="${REGION:-us-east5}"
ACCOUNT="${ACCOUNT:-sourav.jhinjha@gmail.com}"
SERVICE="${SERVICE:-outreach-dashboard}"
API_SERVICE="${API_SERVICE:-outreach-api}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/outreach/dashboard:$(git rev-parse --short HEAD)"
GC=(--project="$PROJECT" --account="$ACCOUNT")

API_URL="$(gcloud run services describe "$API_SERVICE" --region="$REGION" "${GC[@]}" --format='value(status.url)')"

echo "Building $IMAGE (API: $API_URL)"
gcloud builds submit dashboard --config=dashboard/cloudbuild.yaml \
  --substitutions="_IMAGE=${IMAGE},_API_ORIGIN=${API_URL}" "${GC[@]}"

# --timeout=3600: the Run agent page holds a server-sent-events stream open for the whole run.
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" --region="$REGION" "${GC[@]}" \
  --allow-unauthenticated \
  --cpu=1 --memory=512Mi --min-instances=0 --max-instances=2 --timeout=3600 \
  --set-env-vars="API_ORIGIN=${API_URL}"

DASHBOARD_URL="$(gcloud run services describe "$SERVICE" --region="$REGION" "${GC[@]}" --format='value(status.url)')"

# Sign-in must start and finish on the dashboard's origin so the session cookie is set there.
gcloud run services update "$API_SERVICE" --region="$REGION" "${GC[@]}" \
  --update-env-vars="PUBLIC_BASE_URL=${DASHBOARD_URL},POST_LOGIN_REDIRECT=/"

echo "Deployed: $DASHBOARD_URL"
echo "Google OAuth redirect URI to allow: ${DASHBOARD_URL}/auth/callback"
