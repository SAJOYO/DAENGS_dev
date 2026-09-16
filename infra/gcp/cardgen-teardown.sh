#!/usr/bin/env bash
# infra/gcp/cardgen-teardown.sh
# 도감 카드 생성 GPU 서비스 삭제 (D-078). 11-17 크레딧 만료 전에 반드시 — 그 뒤는 자동 실비다.
#   PROJECT=daengs bash infra/gcp/cardgen-teardown.sh
set -euo pipefail

: "${PROJECT:?GCP 프로젝트 id}"
GPU_REGION=asia-southeast1
REGION=asia-northeast3
BUCKET="daengs-cardgen-weights"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/daengs/cardgen"
export CLOUDSDK_CORE_PROJECT="${PROJECT}"

for s in daengs-cardgen-klein; do
  gcloud run services delete "$s" --region="${GPU_REGION}" --quiet || true
done
gcloud run jobs delete cardgen-weights --region="${GPU_REGION}" --quiet || true

echo "== 가중치 버킷 삭제 (수십 GB — 보관료가 계속 나간다)"
gcloud storage rm --recursive "gs://${BUCKET}" --quiet || true

echo "== cardgen 이미지 태그 삭제 (저장소 daengs 자체는 파이프라인과 공유라 안 지운다)"
IMAGES="$(gcloud artifacts docker images list "${IMAGE_BASE}" --format='value(IMAGE)' 2>/dev/null || true)"
if [ -n "${IMAGES}" ]; then
  while IFS= read -r img; do
    [ -n "$img" ] && { gcloud artifacts docker images delete "$img" --delete-tags --quiet || true; }
  done <<< "${IMAGES}"
fi
echo "끝. 서비스 계정·Artifact Registry 저장소는 코퍼스 파이프라인과 공유라 남긴다."
