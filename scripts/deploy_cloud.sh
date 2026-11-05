#!/usr/bin/env bash
# Ejecutar desde la raíz del repositorio en Google Cloud Shell, tras leer CLOUD.md.
set -euo pipefail
: "${CORTE_PROJECT:?Define CORTE_PROJECT con tu proyecto de Google Cloud}"
: "${CORTE_BUCKET:?Define CORTE_BUCKET con el bucket privado de coordinación}"
CORTE_REGION="${CORTE_REGION:-us-east1}"
CORTE_SERVICE="${CORTE_SERVICE:-corte-sk2}"
CORTE_ACCOUNT="corte-sk2-runtime@${CORTE_PROJECT}.iam.gserviceaccount.com"
test -f cloud.env.yaml
python - <<'PY'
import json, os
with open('cloud.env.yaml', encoding='utf-8') as f:
    config = json.load(f)
if config['CORTE_LOCK_BUCKET'] != os.environ['CORTE_BUCKET']:
    raise SystemExit('El bucket de cloud.env.yaml y CORTE_BUCKET no coincide.')
PY
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com sheets.googleapis.com storage.googleapis.com --project "$CORTE_PROJECT"
if ! gcloud iam service-accounts describe "$CORTE_ACCOUNT" --project "$CORTE_PROJECT" >/dev/null 2>&1; then
    gcloud iam service-accounts create corte-sk2-runtime --display-name 'Corte SK2 runtime' --project "$CORTE_PROJECT"
fi
if ! gcloud storage buckets describe "gs://${CORTE_BUCKET}" --project "$CORTE_PROJECT" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${CORTE_BUCKET}" --location "$CORTE_REGION" --uniform-bucket-level-access --public-access-prevention --project "$CORTE_PROJECT"
fi
gcloud storage buckets add-iam-policy-binding "gs://${CORTE_BUCKET}" --member "serviceAccount:${CORTE_ACCOUNT}" --role roles/storage.objectAdmin
printf '\nComparte el Sheet como EDITOR con: %s\n' "$CORTE_ACCOUNT"
read -r -p 'Cuando esté compartido y el servidor local esté detenido, pulsa Enter: '
gcloud run deploy "$CORTE_SERVICE" --source . --region "$CORTE_REGION" --project "$CORTE_PROJECT" --service-account "$CORTE_ACCOUNT" --env-vars-file cloud.env.yaml --allow-unauthenticated --concurrency 1 --max-instances 1 --min-instances 0 --memory 512Mi --timeout 120
gcloud run services describe "$CORTE_SERVICE" --region "$CORTE_REGION" --project "$CORTE_PROJECT" --format='value(status.url)'
