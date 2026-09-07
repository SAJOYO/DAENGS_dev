#!/usr/bin/env bash
# GCP 코퍼스 파이프라인 리소스 (D-062, docs/deploy/corpus-pipeline.md §4).
# 여러 번 돌려도 안전하게 — 있으면 건너뛰거나 update 한다.
#
#   PROJECT=my-proj VM_INTERNAL_IP=10.178.0.2 bash infra/gcp/pipeline.sh
#
# 사람이 먼저 할 것 (infra/gcp/README.md): API 켜기 · Secret 값 넣기 · 초기 사본 업로드.
# `gcloud config set project` 로 사용자의 기본 프로젝트를 바꾸지 않는다 — `CLOUDSDK_CORE_PROJECT`
# 환경변수를 이 스크립트 프로세스에만 export 한다. 모든 gcloud 명령이 이 변수를 본다.
set -euo pipefail

: "${PROJECT:?GCP 프로젝트 id}"
: "${VM_INTERNAL_IP:?pgvector 가 있는 VM 의 내부 IP (gcloud compute instances list)}"
REGION=asia-northeast3          # 서울 — 버킷·refresh 잡·Scheduler
GPU_REGION=asia-southeast1      # 싱가포르 — Cloud Run Jobs 의 L4 가 있는 가장 가까운 리전
BUCKET="daengs-corpus"          # 버킷 이름은 프로젝트가 아니라 전역이다 — create 가 409/403 이면
                                 # 이미 남이 쓰고 있는 것이니 다른 이름으로 바꾼다
REPO="daengs"
SA="corpus-pipeline"
SA_EMAIL="${SA}@${PROJECT}.iam.gserviceaccount.com"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/pipeline"
SHA="$(git rev-parse --short HEAD)"

export CLOUDSDK_CORE_PROJECT="${PROJECT}"

echo "== APIs"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com \
  monitoring.googleapis.com logging.googleapis.com

echo "== Cloud Build 권한 (2024년 중반 이후 만든 프로젝트엔 legacy Cloud Build SA 가 없다 —"
echo "   빌드가 기본 컴퓨트 SA 로 돌고, 권한이 없으면 아래 이미지 빌드가 실패한다)"
# add-iam-policy-binding 은 멱등이라 여러 번 불러도 안전하다 — 이미 있으면 그대로 둔다.
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for role in roles/cloudbuild.builds.builder roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT}" --member="serviceAccount:${BUILD_SA}" --role="$role" >/dev/null
done

echo "== 버킷 (버전 관리 켬 — 잘못된 적재를 되돌리는 유일한 길)"
gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${BUCKET}" --location="${REGION}" --uniform-bucket-level-access
gcloud storage buckets update "gs://${BUCKET}" --versioning

echo "== Artifact Registry"
gcloud artifacts repositories describe "${REPO}" --location="${REGION}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "${REPO}" --location="${REGION}" --repository-format=docker

echo "== 서비스 계정"
gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SA}" --display-name="corpus pipeline job"
# 방금 만든 SA 는 IAM 에 몇 초 뒤에 보인다 — 바로 바인딩하면 "존재하지 않음"으로 실패할 수 있다.
for i in 1 2 3 4 5 6; do gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1 && break; sleep 5; done
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/storage.objectAdmin >/dev/null
ok=""                                                                            # 동시 실행 확인
for i in 1 2 3; do
  if gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${SA_EMAIL}" --role=roles/run.viewer >/dev/null; then
    ok=1
    break
  fi
  sleep 10
done
# 루프의 마지막 명령이 항상 `sleep 10`(성공)이면 3번 다 실패해도 set -e 가 못 잡는다 —
# 성공 플래그로 직접 확인해서 크게 실패한다.
[ -n "$ok" ] || { echo "run.viewer 바인딩 실패 — SA 가 아직 안 보이거나 권한 문제" >&2; exit 1; }
for s in corpus-db-password corpus-law-oc corpus-data-go-kr-key; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || gcloud secrets create "$s" --replication-policy=automatic
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:${SA_EMAIL}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

echo "== 방화벽: 서울 서브넷 → VM 5432 (인터넷에는 여전히 안 연다)"
SUBNET_RANGE="$(gcloud compute networks subnets describe default --region="${REGION}" --format='value(ipCidrRange)')"
gcloud compute firewall-rules describe allow-pg-from-run >/dev/null 2>&1 || \
  gcloud compute firewall-rules create allow-pg-from-run --network=default --direction=INGRESS \
    --action=ALLOW --rules=tcp:5432 --source-ranges="${SUBNET_RANGE}" \
    --description="Cloud Run corpus job -> pgvector on VM (D-062)"

echo "== 이미지 (Cloud Build 가 굽는다 — 개발 PC 에서 7GB 를 올리지 않는다)"
# `--tag` 는 루트의 Dockerfile 만 보므로 config 로 -f 를 준다. 컨텍스트는 저장소 루트.
# `--config=-` (표준입력) 대신 임시 파일을 쓴다 — 이 gcloud 버전에서 stdin 지원이 확인되지 않았다.
# `env: DOCKER_BUILDKIT=1` 도 준다 — 레거시 빌더는 Dockerfile 별 `.dockerignore` 를 무시하고
# 모든 스테이지를 다 빌드해서, cpu 빌드 안에서도 torch-cuda 스테이지의 cu126 설치가 돌아 버린다.
build_image() {   # $1 = cpu|cuda
  local cfg
  cfg="$(mktemp)"
  trap 'rm -f "$cfg"' RETURN
  cat > "$cfg" <<CFG
steps:
- name: gcr.io/cloud-builders/docker
  env: ['DOCKER_BUILDKIT=1']
  args: ['build','-f','docker/pipeline/Dockerfile','--build-arg','TORCH=$1','-t','${IMAGE_BASE}:$1-${SHA}','.']
images: ['${IMAGE_BASE}:$1-${SHA}']
options:
  machineType: E2_HIGHCPU_8
CFG
  gcloud builds submit . --timeout=2400 --config="$cfg"
}
build_image cpu
# cuda 판은 이 스크립트로 처음 굽는다 — CPU 판만 로컬에서 확인됐다. 실패하면 먼저
# docker/pipeline/Dockerfile 의 `uv pip install ... cu126` 단계(torch-cuda 스테이지)를 본다.
build_image cuda

COMMON_ENV="DAENGS_GCP_PROJECT=${PROJECT},POSTGRES_IP=${VM_INTERNAL_IP},POSTGRES_PORT=5432,POSTGRES_USER=daengs,POSTGRES_DB=vectordb,EMBEDDING_MODEL_KEY=qwen3-embedding-0.6b"
COMMON_SECRETS="POSTGRES_PASSWORD=corpus-db-password:latest,LAW_OC=corpus-law-oc:latest,DATA_GO_KR_KEY=corpus-data-go-kr-key:latest"

echo "== Job corpus-refresh (서울, CPU)"
gcloud run jobs deploy corpus-refresh --region="${REGION}" --image="${IMAGE_BASE}:cpu-${SHA}" \
  --service-account="${SA_EMAIL}" --cpu=4 --memory=16Gi --task-timeout=3h --max-retries=0 \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --add-volume=name=corpus,type=cloud-storage,bucket="${BUCKET}" --add-volume-mount=volume=corpus,mount-path=/data \
  --set-env-vars="${COMMON_ENV},DAENGS_GCP_REGION=${REGION}" --set-secrets="${COMMON_SECRETS}"

echo "== Job corpus-embed-full (싱가포르, L4)"
gcloud run jobs deploy corpus-embed-full --region="${GPU_REGION}" --image="${IMAGE_BASE}:cuda-${SHA}" \
  --service-account="${SA_EMAIL}" --cpu=8 --memory=32Gi --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy \
  --task-timeout=2h --max-retries=0 \
  --add-volume=name=corpus,type=cloud-storage,bucket="${BUCKET}" --add-volume-mount=volume=corpus,mount-path=/data \
  --set-env-vars="${COMMON_ENV},DAENGS_GCP_REGION=${GPU_REGION}" --set-secrets="${COMMON_SECRETS}" \
  --args="--stages,embed,--full"

echo "== Scheduler (매일 04:00 KST → corpus-refresh)"
gcloud run jobs add-iam-policy-binding corpus-refresh --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/run.invoker >/dev/null
JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/corpus-refresh:run"
if gcloud scheduler jobs describe corpus-refresh-daily --location="${REGION}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http corpus-refresh-daily --location="${REGION}" \
    --schedule="0 4 * * *" --time-zone="Asia/Seoul" --uri="${JOB_URI}" --http-method=POST \
    --oauth-service-account-email="${SA_EMAIL}"
else
  gcloud scheduler jobs create http corpus-refresh-daily --location="${REGION}" \
    --schedule="0 4 * * *" --time-zone="Asia/Seoul" --uri="${JOB_URI}" --http-method=POST \
    --oauth-service-account-email="${SA_EMAIL}"
fi

echo "== 알림: 잡 실행 실패 → 이메일 (채널은 콘솔에서 한 번 만들어 CHANNEL 로 준다)"
if [ -n "${CHANNEL:-}" ]; then
  if [ -z "$(gcloud monitoring policies list --filter='displayName="corpus job failed"' --format='value(name)')" ]; then
    gcloud monitoring policies create --display-name="corpus job failed" \
      --notification-channels="${CHANNEL}" --combiner=OR \
      --condition-display-name="Cloud Run Job execution failed" \
      --condition-filter='resource.type="cloud_run_job" AND metric.type="run.googleapis.com/job/completed_task_attempt_count" AND metric.labels.result="failed"' \
      --if="> 0" --duration=0s
  else
    echo "(알림 정책 'corpus job failed' 이미 있음)"
  fi
fi

echo "끝. 다음: infra/gcp/README.md 의 '초기 사본' 과 '검증'."
