#!/usr/bin/env bash
# 11-17 실험 종료 (roadmap §8). 버킷은 **마지막에, 확인 뒤** 지운다 — 되돌릴 수 없다.
set -euo pipefail
: "${PROJECT:?}"
REGION=asia-northeast3; GPU_REGION=asia-southeast1
gcloud config set project "${PROJECT}" >/dev/null
gcloud scheduler jobs delete corpus-refresh-daily --location="${REGION}" --quiet || true
gcloud run jobs delete corpus-refresh --region="${REGION}" --quiet || true
gcloud run jobs delete corpus-embed-full --region="${GPU_REGION}" --quiet || true
gcloud compute firewall-rules delete allow-pg-from-run --quiet || true
gcloud artifacts repositories delete daengs --location="${REGION}" --quiet || true
for s in corpus-db-password corpus-law-oc corpus-data-go-kr-key; do gcloud secrets delete "$s" --quiet || true; done
gcloud iam service-accounts delete "corpus-pipeline@${PROJECT}.iam.gserviceaccount.com" --quiet || true
echo "버킷 gs://daengs-corpus 는 남겨 두었다. 정말 지우려면:"
echo "  gcloud storage rm -r gs://daengs-corpus"
