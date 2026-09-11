#!/usr/bin/env bash
# 실시간 산책·날씨 서비스 삭제 (D-068).
#   PROJECT=daengs bash infra/gcp/realtime-teardown.sh
#
# `gcloud config set project` 로 사용자의 기본 프로젝트를 바꾸지 않는다 — `CLOUDSDK_CORE_PROJECT`
# 환경변수를 이 스크립트 프로세스에만 export 한다. 모든 gcloud 명령이 이 변수를 본다.
set -euo pipefail

: "${PROJECT:?GCP 프로젝트 id}"
REGION=asia-northeast3
SERVICE=daengs-realtime
export CLOUDSDK_CORE_PROJECT="${PROJECT}"

gcloud run services delete "${SERVICE}" --region="${REGION}" --quiet || true

# 이 카드가 만든 시크릿 셋만 지운다. ⚠ corpus-* 시크릿(예: corpus-data-go-kr-key)은
# 코퍼스 파이프라인 것이라 여기서 건드리지 않는다 — realtime.sh 도 그것을 새로 만들지 않고
# IAM 바인딩만 재사용했다.
for s in realtime-redis-url realtime-kakao-key realtime-kma-hub-key; do
  gcloud secrets delete "$s" --quiet || true
done

# 방화벽 규칙은 애초에 안 만들었으므로 지울 것도 없다 (realtime.sh 의 주석 참고 —
# default-allow-internal 이 이미 덮는다).

# Artifact Registry 저장소(daengs)·서비스 계정(corpus-pipeline@...)은 코퍼스 파이프라인과
# 공유하는 자원이라 여기서 지우지 않는다 — pipeline-teardown.sh 가 관리한다.

echo "끝. 남긴 것: Artifact Registry 저장소(daengs), 서비스 계정(corpus-pipeline@${PROJECT}...) —"
echo "코퍼스 파이프라인과 공유하므로 이 스크립트가 지우지 않는다. 정말 지우려면"
echo "infra/gcp/pipeline-teardown.sh 를 보고 사람이 판단해서 돌려라."
