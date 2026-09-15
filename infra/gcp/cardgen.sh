#!/usr/bin/env bash
# infra/gcp/cardgen.sh
# 도감 카드 생성 GPU 서비스 배포 (D-078). 모델마다 서비스 하나.
#   MODEL=klein PROJECT=daengs INVOKER=user:<gcloud 계정> bash infra/gcp/cardgen.sh
#   STEP=image|weights|deploy 로 한 단계만 (기본: 전부)
#
# ⚠ 돈이 나간다 — Cloud Build(CUDA 이미지 약 10GB), 가중치 받기 잡, 서비스가 떠 있는 L4 시간.
#   **돌리기 전에 사람에게 무엇을 몇 번 하는지 설명하고 승인받는다** (#544).
# ⚠ Git Bash 에서 돌린다. `MSYS_NO_PATHCONV=1` 을 켜지 마라 — gcloud 자체가 깨진다 (infra/gcp/README.md).
set -euo pipefail

: "${MODEL:?klein 또는 qwen}"
: "${INVOKER:?user:<gcloud 계정> — gcloud run services proxy 로 부를 사람}"
PROJECT="${PROJECT:-daengs}"
STEP="${STEP:-all}"
case "${MODEL}" in
  klein) MODEL_NAME=klein-4b ;;
  qwen)  MODEL_NAME=qwen-edit-2511 ;;
  *) echo "MODEL 은 klein 또는 qwen" >&2; exit 2 ;;
esac

REGION=asia-northeast3        # 이미지 저장소(daengs)는 서울 — 코퍼스·realtime 과 공유
GPU_REGION=asia-southeast1    # Cloud Run L4 가 있는 가장 가까운 리전 (서울엔 없다)
SERVICE="daengs-cardgen-${MODEL}"
BUCKET="daengs-cardgen-weights"
SA_EMAIL="corpus-pipeline@${PROJECT}.iam.gserviceaccount.com"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/daengs/cardgen"
export CLOUDSDK_CORE_PROJECT="${PROJECT}"
# --command=/opt/venv/bin/python 도 Git Bash 가 Windows 경로로 바꾼다 (README 「자주 걸리는 것」①).
# --set-env-vars=HF_XET_CACHE=/tmp/xet 도 마찬가지다 — 2026-09-15 실측: 잡에 `C:/Users/403/AppData/Local/Temp/xet`
# 로 저장됐다(#544 Task 6, `jobs describe` 로 발견). 경로를 담는 인자는 전부 이 목록에 넣는다.
export MSYS2_ARG_CONV_EXCL="--add-volume-mount;--command;--set-env-vars"

# 태그는 이미지 입력의 내용 해시 (realtime.sh 와 같은 규칙). 목록이 곧 계약이다.
SHA="$(git ls-files -s backend/pyproject.toml backend/uv.lock backend/README.md \
        backend/src/daengs_cardgen docker/cardgen | git hash-object --stdin | cut -c1-7)"
IMAGE="${IMAGE_BASE}:${SHA}"

if [ "${STEP}" = all ] || [ "${STEP}" = image ]; then
  echo "== 이미지 (Cloud Build)"
  if gcloud artifacts docker images describe "${IMAGE}" >/dev/null 2>&1; then
    echo "(이미지 ${IMAGE} 이미 있음 — 빌드 생략)"
  else
    cfg="$(mktemp)"
    cat > "$cfg" <<CFG
steps:
  - name: gcr.io/cloud-builders/docker
    env: ['DOCKER_BUILDKIT=1']
    args: ['build', '-f', 'docker/cardgen/Dockerfile', '-t', '${IMAGE}', '.']
images: ['${IMAGE}']
options:
  machineType: 'E2_HIGHCPU_8'
  diskSizeGb: '100'
CFG
    gcloud builds submit --config="$cfg" .
    rm -f "$cfg"
  fi
fi

if [ "${STEP}" = all ] || [ "${STEP}" = weights ]; then
  echo "== 가중치 버킷 (${GPU_REGION})"
  gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || \
    gcloud storage buckets create "gs://${BUCKET}" --location="${GPU_REGION}" --uniform-bucket-level-access
  gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
    --member="serviceAccount:${SA_EMAIL}" --role=roles/storage.objectAdmin >/dev/null
  echo "== 가중치 받기 잡 (CPU, 한 번)"
  # HF_XET_CACHE 를 /tmp 로 뺀다 — 안 그러면 hf_xet 의 청크 캐시가 HF_HOME(=FUSE 버킷)에 작은 객체를 잔뜩 쓴다.
  gcloud run jobs deploy cardgen-weights --region="${GPU_REGION}" --image="${IMAGE}" \
    --service-account="${SA_EMAIL}" --cpu=8 --memory=32Gi --task-timeout=3h --max-retries=0 \
    --command=/opt/venv/bin/python --args=-m,daengs_cardgen.fetch,"${MODEL_NAME}" \
    --set-env-vars=HF_HUB_DISABLE_XET=1 \
    --add-volume=name=weights,type=cloud-storage,bucket="${BUCKET}" \
    --add-volume-mount=volume=weights,mount-path=/models
  gcloud run jobs execute cardgen-weights --region="${GPU_REGION}" --wait \
    --args=-m,daengs_cardgen.fetch,"${MODEL_NAME}"
fi

if [ "${STEP}" = all ] || [ "${STEP}" = deploy ]; then
  echo "== 서비스 배포 (${SERVICE})"
  # min 0 · max 1: 요청이 없으면 0대(0원). GPU 서비스는 인스턴스 기반 과금이라 떠 있는 동안은 유휴도 과금된다.
  # 포트는 곧바로 열리고 모델은 백그라운드로 올라간다(daengs_cardgen/app.py) — 시작 프로브는 기본 TCP 로 충분하다.
  # Cloud Run 시작 프로브는 240초가 상한이라 "다 올린 뒤 포트를 연다" 는 Qwen 에서 못 맞춘다.
  # 올리는 동안 온 요청은 앱이 최대 840초 기다린다(--timeout=900 안쪽). 진행은 /health 의 ready·error.
  gcloud run deploy "${SERVICE}" --region="${GPU_REGION}" --image="${IMAGE}" \
    --service-account="${SA_EMAIL}" --no-allow-unauthenticated \
    --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy \
    --cpu=8 --memory=32Gi --no-cpu-throttling \
    --concurrency=1 --min-instances=0 --max-instances=1 --timeout=900 \
    --add-volume=name=weights,type=cloud-storage,bucket="${BUCKET}",readonly=true \
    --add-volume-mount=volume=weights,mount-path=/models \
    --set-env-vars="CARDGEN_MODEL=${MODEL_NAME},HF_HUB_OFFLINE=1,CARDGEN_QWEN_QUANT=${CARDGEN_QWEN_QUANT:-nf4}"
  gcloud run services add-iam-policy-binding "${SERVICE}" --region="${GPU_REGION}" \
    --member="${INVOKER}" --role=roles/run.invoker >/dev/null
  URL="$(gcloud run services describe "${SERVICE}" --region="${GPU_REGION}" --format='value(status.url)')"
  echo
  echo "완료: ${URL}"
  echo "개발 PC 에서 부르기 (PowerShell):"
  echo "  gcloud run services proxy ${SERVICE} --region=${GPU_REGION} --port=8091"
  echo "  curl.exe -s http://127.0.0.1:8091/health"
fi
