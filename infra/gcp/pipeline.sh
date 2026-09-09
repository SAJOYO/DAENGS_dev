#!/usr/bin/env bash
# GCP 코퍼스 파이프라인 리소스 (D-062, docs/deploy/corpus-pipeline.md §4).
# 여러 번 돌려도 안전하게 — 있으면 건너뛰거나 update 한다.
#
#   PROJECT=my-proj VM_INTERNAL_IP=10.178.0.2 bash infra/gcp/pipeline.sh
#
# VM 이름·존은 기본값이 있다 (`VM_NAME`·`VM_ZONE`). 관리자 트리거가 설 수 있는 상태인지
# **확인만** 하는 데 쓴다 — 아래 "VM 액세스 범위" 절. `SKIP_SCOPE_CHECK=1` 로 끌 수 있다.
#
# 사람이 먼저 할 것 (infra/gcp/README.md): API 켜기 · Secret 값 넣기 · 초기 사본 업로드.
# `gcloud config set project` 로 사용자의 기본 프로젝트를 바꾸지 않는다 — `CLOUDSDK_CORE_PROJECT`
# 환경변수를 이 스크립트 프로세스에만 export 한다. 모든 gcloud 명령이 이 변수를 본다.
set -euo pipefail

# Windows Git Bash(MSYS)가 네이티브 exe 인자의 `/data` 같은 경로를 `C:/Program Files/Git/data` 로 바꿔
# 버려 mount-path 가 깨진다 (2026-09-08 실측). MSYS_NO_PATHCONV=1 로 전부 끄면 gcloud 런처(bash
# 스크립트)가 python 에 넘기는 경로까지 안 바뀌어 gcloud 자체가 안 뜬다. 그래서 그 인자 하나만 제외한다.
# 리눅스에서는 아무 효과 없다.
export MSYS2_ARG_CONV_EXCL="--add-volume-mount"

: "${PROJECT:?GCP 프로젝트 id}"
: "${VM_INTERNAL_IP:?pgvector 가 있는 VM 의 내부 IP (gcloud compute instances list)}"
VM_NAME="${VM_NAME:-daengs}"    # 관리자 트리거를 거는 backend 가 도는 VM (범위 확인에만 쓴다)
VM_ZONE="${VM_ZONE:-asia-northeast3-c}"
REGION=asia-northeast3          # 서울 — 버킷·refresh 잡·Scheduler
GPU_REGION=asia-southeast1      # 싱가포르 — Cloud Run Jobs 의 L4 가 있는 가장 가까운 리전
BUCKET="daengs-corpus"          # 버킷 이름은 프로젝트가 아니라 전역이다 — create 가 409/403 이면
                                 # 이미 남이 쓰고 있는 것이니 다른 이름으로 바꾼다
REPO="daengs"
SA="corpus-pipeline"
SA_EMAIL="${SA}@${PROJECT}.iam.gserviceaccount.com"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/pipeline"
# 이미지 태그는 커밋이 아니라 **이미지에 들어가는 파일의 내용 해시**다. 커밋마다 굽던 것을
# (문서·스크립트만 바뀌어도 5~23분씩) backend/·Dockerfile·시드가 바뀔 때만 굽게 한다.
# `git ls-files -s` 는 그 경로들 아래 추적 파일들의 blob id 를 찍고, 그걸 해시한다 —
# 그 파일들의 커밋된 내용이 바뀔 때만 값이 바뀐다. 커밋되지 않은 수정은 반영되지 않는다 —
# 커밋된 내용 기준이다.
SHA="$(git ls-files -s backend/pyproject.toml backend/uv.lock backend/README.md backend/src \
  data/manifests/seed_sources.yaml docker/pipeline | git hash-object --stdin | cut -c1-7)"

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
for s in corpus-db-password corpus-law-oc corpus-data-go-kr-key corpus-seoul-open-data-key; do
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
  # 태그에 내용 해시가 들어 있으므로, 같은 태그가 이미 있으면 같은 소스라는 뜻이다 — 다시 안 굽는다.
  # 이 확인이 없으면 스크립트를 다시 돌릴 때마다(코드가 안 바뀌었어도) 이미지 두 장을 5~23분씩 또 굽는다.
  if gcloud artifacts docker images describe "${IMAGE_BASE}:$1-${SHA}" >/dev/null 2>&1; then
    echo "(이미지 ${IMAGE_BASE}:$1-${SHA} 이미 있음 — 빌드 생략)"
    return 0
  fi
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
COMMON_SECRETS="POSTGRES_PASSWORD=corpus-db-password:latest,LAW_OC=corpus-law-oc:latest,DATA_GO_KR_KEY=corpus-data-go-kr-key:latest,SEOUL_OPEN_DATA_KEY=corpus-seoul-open-data-key:latest"

echo "== Job corpus-refresh (서울, CPU)"
gcloud run jobs deploy corpus-refresh --region="${REGION}" --image="${IMAGE_BASE}:cpu-${SHA}" \
  --service-account="${SA_EMAIL}" --cpu=4 --memory=16Gi --task-timeout=3h --max-retries=0 \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --add-volume=name=corpus,type=cloud-storage,bucket="${BUCKET}" --add-volume-mount=volume=corpus,mount-path=/data \
  --set-env-vars="${COMMON_ENV},DAENGS_GCP_REGION=${REGION}" --set-secrets="${COMMON_SECRETS}"

echo "== Job corpus-embed-full (싱가포르, L4)"
# Cloud Run GPU 잡은 타임아웃 상한이 1시간이다(2026-09-08 실측). L4 로 1만 청크는 10~20분이라 들어간다.
gcloud run jobs deploy corpus-embed-full --region="${GPU_REGION}" --image="${IMAGE_BASE}:cuda-${SHA}" \
  --service-account="${SA_EMAIL}" --cpu=8 --memory=32Gi --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy \
  --task-timeout=1h --max-retries=0 \
  --add-volume=name=corpus,type=cloud-storage,bucket="${BUCKET}" --add-volume-mount=volume=corpus,mount-path=/data \
  --set-env-vars="${COMMON_ENV},DAENGS_GCP_REGION=${GPU_REGION}" --set-secrets="${COMMON_SECRETS}" \
  --args="--stages,embed,--full"

echo "== Scheduler (매일 04:00 KST → corpus-refresh)"
gcloud run jobs add-iam-policy-binding corpus-refresh --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/run.invoker >/dev/null

echo "== 관리자 콘솔 트리거(#326) — VM 의 backend 가 이 계정으로 잡을 실행·조회한다"
# VM 은 기본 컴퓨트 SA(=BUILD_SA, 위에서 이미 계산)로 메타데이터 서버 인증을 쓴다. 이 바인딩이
# README 산문에만 있으면 teardown 뒤 재배포 때 조용히 빠진다 — 여기 스크립트에 있어야
# `pipeline.sh` 한 번으로 항상 같이 생긴다. add-iam-policy-binding 은 멱등이다.
gcloud run jobs add-iam-policy-binding corpus-refresh --region="${REGION}" \
  --member="serviceAccount:${BUILD_SA}" --role=roles/run.invoker >/dev/null
gcloud run jobs add-iam-policy-binding corpus-refresh --region="${REGION}" \
  --member="serviceAccount:${BUILD_SA}" --role=roles/run.viewer >/dev/null

# ── VM 액세스 범위 — **역할만으로는 안 선다** (2026-09-09 실측, #326 후속)
#
# 위 바인딩이 다 성공해도 버튼이 503 이고 상태 페이지가 down 일 수 있다. 막는 것이 역할이
# 아니라 **인스턴스에 박힌 OAuth 범위**이기 때문이다 — 메타데이터 서버가 내주는 토큰의 범위에
# run.googleapis.com 이 없으면 IAM 을 아무리 줘도 못 부른다:
#
#     PermissionDenied: 403 Request had insufficient authentication scopes.
#     reason: "ACCESS_TOKEN_SCOPE_INSUFFICIENT"  method: google.cloud.run.v2.Jobs.GetJob
#
# GCE 기본값(devstorage.read_only·logging.write·monitoring.write·…)에는 안 들어 있다.
#
# ⚠ **여기서 고치지 않는다.** `set-service-account` 는 인스턴스가 TERMINATED 여야 먹으므로
# 고치는 것은 운영을 몇 분 내리는 일이고, 배포 스크립트가 말없이 할 일이 아니다. 감지해서
# 알려 주는 데까지다. 그래서 실패로 끝내지도 않는다 — 첫 설치 때는 VM 이 아직 없을 수 있다.
SCOPE_WARNING=""
if [ -n "${SKIP_SCOPE_CHECK:-}" ]; then
  echo "== VM 액세스 범위 확인 건너뜀 (SKIP_SCOPE_CHECK)"
else
  echo "== VM 액세스 범위 확인 — ${VM_NAME}/${VM_ZONE}"
  VM_SCOPES="$(gcloud compute instances describe "${VM_NAME}" --zone="${VM_ZONE}" \
                 --format='value(serviceAccounts[0].scopes)' 2>/dev/null || true)"
  case "${VM_SCOPES}" in
    *cloud-platform*)
      echo "   ok — cloud-platform 이 있다" ;;
    "")
      echo "   (VM 을 못 찾았다 — 아직 안 세웠거나 이름·존이 다르다. VM_NAME·VM_ZONE 으로 준다)" ;;
    *)
      SCOPE_WARNING="yes"
      echo "   🔴 cloud-platform 이 없다 — 관리자 트리거가 안 선다" ;;
  esac
fi

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

# 경고를 마지막에 **한 번 더** 찍는다 — 이 스크립트는 로그가 길어서 중간에 낸 것은 묻힌다.
# 실패로 끝내지 않는 대신(위 주석) 사람 눈에 남는 자리를 여기로 잡았다.
if [ -n "${SCOPE_WARNING}" ]; then
  cat >&2 <<MSG

🔴 확인 필요 — VM(${VM_NAME}) 의 액세스 범위에 cloud-platform 이 없다.
   위에서 건 IAM 역할은 맞지만 **관리자 콘솔의 크롤 버튼은 503 이고 상태 페이지는 down** 이다
   (ACCESS_TOKEN_SCOPE_INSUFFICIENT). 범위는 인스턴스를 멈춰야 바뀐다 — 운영이 몇 분 내려간다:

     gcloud compute instances stop ${VM_NAME} --zone=${VM_ZONE}
     gcloud compute instances set-service-account ${VM_NAME} --zone=${VM_ZONE} \\
       --service-account=${BUILD_SA} --scopes=https://www.googleapis.com/auth/cloud-platform
     gcloud compute instances start ${VM_NAME} --zone=${VM_ZONE}

   ⚠ 멈추기 전에 **외부 IP 가 예약된 고정 주소인지** 볼 것 — 임시 IP 면 정지만으로 주소를 잃고
     DNS 가 끊긴다:  gcloud compute addresses list
   자세한 것은 infra/gcp/README.md 의 "IAM 만으로는 안 된다".
MSG
fi

echo "끝. 다음: infra/gcp/README.md 의 '초기 사본' 과 '검증'."
