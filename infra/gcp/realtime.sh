#!/usr/bin/env bash
# 실시간 산책·날씨 서비스 배포 (D-068).
#   PROJECT=daengs bash infra/gcp/realtime.sh
#
# ⚠ Git Bash 에서 돌린다. `MSYS_NO_PATHCONV=1` 을 켜지 마라 — gcloud 자체가 깨진다
#   (2026-09-11 실측: can't open file 'C:\c\Program Files...gcloud.py').
#
# `gcloud config set project` 로 사용자의 기본 프로젝트를 바꾸지 않는다 — `CLOUDSDK_CORE_PROJECT`
# 환경변수를 이 스크립트 프로세스에만 export 한다. 모든 gcloud 명령이 이 변수를 본다.
set -euo pipefail

PROJECT="${PROJECT:-daengs}"
REGION=asia-northeast3
SERVICE=daengs-realtime
SA_EMAIL="corpus-pipeline@${PROJECT}.iam.gserviceaccount.com"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/daengs/realtime"
VM_INTERNAL_IP="${VM_INTERNAL_IP:-10.178.0.2}"

export CLOUDSDK_CORE_PROJECT="${PROJECT}"

# 태그는 커밋이 아니라 **이미지 입력의 내용 해시**다 (`pipeline.sh` 와 같은 규칙, #427).
# 여기 목록에 없는 파일을 고쳐도 이미지는 안 바뀐다 — 그래서 목록이 곧 계약이다.
SHA="$(git ls-files -s backend/pyproject.toml backend/uv.lock backend/README.md \
        backend/src/daengs_life docker/realtime | git hash-object --stdin | cut -c1-7)"

echo "== 시크릿 (없으면 만들고, 값은 사람이 넣는다)"
for s in realtime-redis-url realtime-kakao-key realtime-kma-hub-key; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || {
    gcloud secrets create "$s" --replication-policy=automatic
    echo "  ⚠ ${s} 가 비어 있다. 값을 넣어라:"
    echo "     printf %s '<값>' | gcloud secrets versions add ${s} --data-file=-"
  }
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:${SA_EMAIL}" --role=roles/secretmanager.secretAccessor >/dev/null
done
# DATA_GO_KR_KEY 는 코퍼스 파이프라인이 이미 만든 시크릿을 그대로 재사용한다 — 새로 안 만든다.
gcloud secrets add-iam-policy-binding corpus-data-go-kr-key \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/secretmanager.secretAccessor >/dev/null

echo "== 이미지 (Cloud Build)"
if gcloud artifacts docker images describe "${IMAGE_BASE}:${SHA}" >/dev/null 2>&1; then
  echo "(이미지 ${IMAGE_BASE}:${SHA} 이미 있음 — 빌드 생략)"
else
  cfg="$(mktemp)"
  cat > "$cfg" <<CFG
steps:
  - name: gcr.io/cloud-builders/docker
    env: ['DOCKER_BUILDKIT=1']
    args: ['build', '-f', 'docker/realtime/Dockerfile', '-t', '${IMAGE_BASE}:${SHA}', '.']
images: ['${IMAGE_BASE}:${SHA}']
CFG
  gcloud builds submit --config="$cfg" .
  rm -f "$cfg"
fi

# ⚠ **방화벽 규칙은 만들지 않는다.** Cloud Run 잡(`corpus-pipeline` SA, `--network=default
#   --subnet=default`)에서 VM 의 6379(Redis) 로 접속하면 `default-allow-internal` 이 이미
#   덮는다 — 2026-09-11 실측: 규칙을 안 만들고 `10.178.0.2:6379` 에 붙였더니
#   `-NOAUTH Authentication required` 가 왔다(연결 자체는 성공, 인증만 남은 응답).
#   `default-allow-internal` 은 `10.128.0.0/9` 를 덮고 서브넷 `default`(`10.178.0.0/20`)가
#   그 안에 든다. **5432(Postgres) 쪽 `allow-pg-from-run` 만 별도 규칙이 있는 이유**는
#   그것이 이 기본 규칙보다 먼저 만들어졌고 `pipeline.sh` 가 이미 관리하고 있기 때문이지,
#   6379 가 막혀 있어서가 아니다. `pipeline.sh` 의 그 규칙은 여기서 건드리지 않는다.

echo "== 서비스 배포"
# --min-instances=0 이 이 카드의 요점이다. 인터넷에는 안 연다 (인증은 backend 가 한다).
gcloud run deploy "${SERVICE}" --region="${REGION}" --image="${IMAGE_BASE}:${SHA}" \
  --service-account="${SA_EMAIL}" \
  --no-allow-unauthenticated \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --min-instances=0 --max-instances=5 \
  --cpu=1 --memory=512Mi --concurrency=40 --timeout=60s \
  --set-secrets="REDIS_URL=realtime-redis-url:latest,DATA_GO_KR_KEY=corpus-data-go-kr-key:latest,KAKAO_REST_KEY=realtime-kakao-key:latest,KMA_HUB_KEY=realtime-kma-hub-key:latest"

echo "== VM 의 backend 계정에 호출 권한"
# VM 은 기본 컴퓨트 SA 로 메타데이터 인증을 쓴다 (#326 과 같은 방식).
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
gcloud run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/run.invoker >/dev/null

URL="$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')"
echo
echo "완료. VM 의 backend/.env 에 이 줄을 넣어라:"
echo "  DAENGS_REALTIME_URL=${URL}"
echo
echo "⚠ 줄을 넣는 것만으로는 안 켜진다. compose 는 backend/.env 를 컨테이너에 마운트하지 않고"
echo "  env_file 로 넣는데, 그 값은 **컨테이너를 만들 때** 굳는다. runbook §6 의 배포"
echo "  (git merge --ff-only) 는 fastapi dev 의 reload 라 컨테이너를 다시 만들지 않으므로,"
echo "  그 경로로는 이 값이 절대 반영되지 않는다."
echo "  켜려면 VM 에서 아래를 **일부러** 돌려야 한다 (셸에 GEMINI_API_KEY 를 먼저 올릴 것 —"
echo "  안 올리면 빈 키가 박혀 의미 라우터가 죽는다. CLAUDE.md 실측):"
echo "    export GEMINI_API_KEY=..."
echo "    docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d backend"
