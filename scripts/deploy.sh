#!/usr/bin/env bash
# Build the API image with Cloud Build (no local Docker needed) and deploy it to Cloud Run.
#
#   bash scripts/deploy.sh
#
# Uses an explicit --account/--project on every call, so the machine's active gcloud config is untouched.
# Run database migrations first: `alembic upgrade head` (the app doesn't migrate on startup).
set -euo pipefail

PROJECT="${PROJECT:-outreach-io-sj26}"
REGION="${REGION:-us-east5}"          # closest GCP region to the Neon database (AWS us-east-2, Ohio)
ACCOUNT="${ACCOUNT:-sourav.jhinjha@gmail.com}"
SERVICE="${SERVICE:-outreach-api}"
BUCKET="${BUCKET:-outreach-io-sj26-files}"
RUNTIME_SA="outreach-api@${PROJECT}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/outreach/api:$(git rev-parse --short HEAD)"
GC=(--project="$PROJECT" --account="$ACCOUNT")

echo "Building $IMAGE"
gcloud builds submit --tag "$IMAGE" "${GC[@]}" .   # global Cloud Build pool; the image goes to the regional registry

SECRETS="DATABASE_URL=DATABASE_URL:latest,VAULT_MASTER_KEY=VAULT_MASTER_KEY:latest,SESSION_SECRET=SESSION_SECRET:latest"
SECRETS+=",GOOGLE_LOGIN_CLIENT_ID=GOOGLE_LOGIN_CLIENT_ID:latest,GOOGLE_LOGIN_CLIENT_SECRET=GOOGLE_LOGIN_CLIENT_SECRET:latest"
SECRETS+=",INITIAL_ADMIN_EMAIL=INITIAL_ADMIN_EMAIL:latest,INTERNAL_TASK_TOKEN=INTERNAL_TASK_TOKEN:latest"

# The service URL is only known after the first deploy; later deploys pass it in for OAuth redirects.
URL="$(gcloud run services describe "$SERVICE" --region="$REGION" "${GC[@]}" --format='value(status.url)' 2>/dev/null || true)"
ENV_VARS="APP_MODE=dev,STORAGE_BACKEND=gcs,GCS_BUCKET=${BUCKET},AUTO_EVALS=true"
[[ -n "$URL" ]] && ENV_VARS+=",PUBLIC_BASE_URL=${URL}"

# Background jobs (discovery runs, verification) live in the process:
#   --no-cpu-throttling  keeps CPU after the response is sent
#   --min-instances=1    so the instance isn't scaled to zero mid-job
#   --max-instances=1    the in-process job queue and restart cleanup assume a single instance
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" --region="$REGION" "${GC[@]}" \
  --service-account="$RUNTIME_SA" \
  --allow-unauthenticated \
  --no-cpu-throttling --min-instances=1 --max-instances=1 \
  --cpu=1 --memory=1Gi --timeout=3600 \
  --set-secrets="$SECRETS" \
  --set-env-vars="$ENV_VARS"

URL="$(gcloud run services describe "$SERVICE" --region="$REGION" "${GC[@]}" --format='value(status.url)')"
echo "Deployed: $URL  (health: $URL/health)"
