#!/usr/bin/env bash
# 11-17 실험 종료 (roadmap §8). 버킷은 **마지막에, 확인 뒤** 지운다 — 되돌릴 수 없다.
# `gcloud config set project` 로 사용자의 기본 프로젝트를 바꾸지 않는다 — `CLOUDSDK_CORE_PROJECT`
# 환경변수를 이 스크립트 프로세스에만 export 한다. 모든 gcloud 명령이 이 변수를 본다.
set -euo pipefail
: "${PROJECT:?}"
REGION=asia-northeast3; GPU_REGION=asia-southeast1
export CLOUDSDK_CORE_PROJECT="${PROJECT}"
gcloud scheduler jobs delete corpus-refresh-daily --location="${REGION}" --quiet || true
gcloud run jobs delete corpus-refresh --region="${REGION}" --quiet || true
gcloud run jobs delete corpus-embed-full --region="${GPU_REGION}" --quiet || true
gcloud compute firewall-rules delete allow-pg-from-run --quiet || true
gcloud artifacts repositories delete daengs --location="${REGION}" --quiet || true
for s in corpus-db-password corpus-law-oc corpus-data-go-kr-key; do gcloud secrets delete "$s" --quiet || true; done
gcloud iam service-accounts delete "corpus-pipeline@${PROJECT}.iam.gserviceaccount.com" --quiet || true
for p in $(gcloud monitoring policies list --filter='displayName="corpus job failed"' --format='value(name)'); do
  gcloud monitoring policies delete "$p" --quiet || true
done
echo "버킷 gs://daengs-corpus 는 남겨 두었다. 정말 지우려면:"
echo "  gcloud storage rm -r gs://daengs-corpus"
