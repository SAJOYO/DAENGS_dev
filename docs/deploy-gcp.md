# GCP 이관 절차서 (D-042)

9/21 발표까지 서빙을 GCP VM 1대로 옮기는 절차입니다. 배경·비용·대안 비교는 노션
"클라우드 이전 검토" 문서, 결정 요약은 `docs/decisions.md` D-042 참고.

**결정 요약** — VM 1대(e2-standard-4, 서울)에 지금 compose 를 통째로. 배포 소스는
`main` 브랜치. TLS 는 certbot(Let's Encrypt, 무료). **서빙만 옮깁니다** — 크롤러·코퍼스는
로컬 서버가 계속 정본이고, 로컬 서버 DB 는 개발용으로 남습니다(dev/prod 분리).
크레딧 잔액 약 ₩435k, **만료 2026-11-17. 만료 후 자동 실비 청구** — 예산 알림 필수.

앱이 부를 주소(앱에 박히는 값, 앱 담당자에게 전달):

```
https://daengback.weareithero.cloud        ← ⚠ 실제 서브도메인은 가비아 DNS 에서 확인
```

---

## Phase 0 — GCP 콘솔 준비 (반나절)

- [ ] 프로젝트 확인, 결제 계정에 **예산 알림 50% / 80% / 100%** (₩435,523 기준)
- [ ] **고정 IP 예약** — asia-northeast3(서울), 이름 `daengs-ip`
- [ ] VM 생성 — `e2-standard-4`(4vCPU/16GB), Ubuntu 24.04 LTS, **100GB pd-balanced**,
      region asia-northeast3, 위 고정 IP 연결
- [ ] 방화벽 — 인바운드 **80 · 443 만 전체 공개**. SSH(22)는 IAP 또는 내 IP 로 제한.
      **5432 · 6379 · 8000 은 열지 않습니다** — 팀 개발 DB 는 로컬 서버를 계속 쓰므로
      열 이유가 없고, 8000 은 호스트 내부(Next 서버사이드 프록시)용입니다.
- [ ] 가비아 DNS 에서 두 서브도메인의 **TTL 을 미리 낮춰 둡니다**(300초) — 전환을 빠르게

## Phase 1 — VM 셋업 + 상태 이전 (1~2일)

### 1-1. 기본 셋업

```bash
# Docker (공식 스크립트) + compose 플러그인
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # 재로그인 필요

# Node LTS + PM2 (프론트는 로컬 서버와 같은 모양 — PM2 호스트 실행)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs
sudo npm i -g pm2

# 배포 폴더 밖 상태 폴더 (로컬 서버의 C:/deploy 와 같은 취급)
sudo mkdir -p /srv/daengs/{models/release,gait/release,letsencrypt,dumps,corpus-unused}
sudo chown -R $USER /srv/daengs

git clone -b main https://github.com/SAJOYO/DAENGS_dev.git ~/daengs && cd ~/daengs
```

### 1-2. 상태 이전 체크리스트

로컬(서버 PC·개발 PC)에서 꺼내 `gcloud compute scp` 로 올립니다.

- [ ] **DB 덤프 3개** — ⚠ 먼저 서버 DB 에 `db/migrations/` 최근 4건(2026-08-31~09-01)이
      적용됐는지 확인하고 덤프하세요. 덤프에 포함되면 GCP 쪽 수동 적용이 필요 없습니다.

  ```powershell
  # 로컬 서버에서. PowerShell 의 > 는 바이너리를 깨뜨리므로 컨테이너 안에 만들고 docker cp
  docker compose exec pgvector sh -c "pg_dumpall -U postgres --globals-only > /tmp/globals.sql"
  docker compose exec pgvector sh -c "pg_dump -U postgres -Fc vectordb > /tmp/vectordb.dump"
  docker cp pgvector:/tmp/globals.sql .; docker cp pgvector:/tmp/vectordb.dump .
  docker compose exec training-rag-pgvector sh -c "pg_dump -U dog_rag -Fc dog_rag > /tmp/dog_rag.dump"
  docker cp daengs-training-pgvector:/tmp/dog_rag.dump .
  docker compose exec place-db sh -c "pg_dump -U place -Fc place > /tmp/place.dump"
  docker cp daengs-place-db:/tmp/place.dump .
  ```

  globals.sql 을 뜨는 이유: `pg_dump` 는 **롤을 담지 않습니다.** 서버 DB 의 `daengs` 롤은
  손으로 만든 것이라 이것 없이 복원하면 소유자가 없어 실패합니다.

- [ ] **가중치 4개** (git 에 없음) → 스크리닝 2개는 `/srv/daengs/models/release/`,
      gait `best.pt`·`yolov8n.pt` 는 `/srv/daengs/gait/release/`
- [ ] **`.env` 2개** — 최상단 `.env` 와 `backend/.env`. 그대로 복사 후 GCP 값으로 수정:

  | 파일 | 항목 | GCP 값 |
  | --- | --- | --- |
  | 최상단 | `SCREENING_RELEASE_DIR` | `/srv/daengs/models/release` |
  | 최상단 | `GAIT_RELEASE_DIR` | `/srv/daengs/gait/release` |
  | 최상단 | `DAENGS_CORPUS_DIR` | `/srv/daengs/corpus-unused` — **더미.** 크롤러를 안 띄워도 compose 가 파일 해석 시점에 `:?` 가드를 평가하므로 값 자체는 있어야 합니다 |
  | backend | `DAENGS_CORS_ORIGINS` | `https://daengs.weareithero.cloud` (프론트 도메인, https) |

- [ ] **암호화 키 3개** (`DAENGS_JWE_KEY`·`DAENGS_AES_KEY`·`DAENGS_BLIND_INDEX_KEY`) —
      **로컬과 같은 값**이어야 덤프해 온 암호문이 열립니다. 새로 만들면 안 됩니다.
- [ ] `GEMINI_API_KEY` · Place/KTO 키 · TMAP 키 — `.env` 에 딸려 오는지 확인
- [ ] 임베딩 모델 가중치(hf-cache 1.2GB)는 옮기지 않습니다 — 첫 기동 때 자동 다운로드.
      `EMBEDDING_MODEL_KEY` 를 바꾸지 않았는지만 확인(코퍼스와 어긋나면 조용히 틀림)

### 1-3. DB 복원 + 기동

```bash
cd ~/daengs
# ① DB 만 먼저 — 빈 볼륨이라 db/init·Alembic 이 스키마를 만듭니다
docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d pgvector training-rag-pgvector place-db

# ② 롤 → 데이터 복원 (--clean 이 init 스키마를 덤프 것으로 갈아 끼웁니다)
docker cp globals.sql pgvector:/tmp/ && docker compose exec pgvector psql -U postgres -f /tmp/globals.sql
docker cp vectordb.dump pgvector:/tmp/ && docker compose exec pgvector pg_restore -U postgres -d vectordb --clean --if-exists /tmp/vectordb.dump
docker cp dog_rag.dump daengs-training-pgvector:/tmp/ && docker compose exec training-rag-pgvector pg_restore -U dog_rag -d dog_rag --clean --if-exists /tmp/dog_rag.dump
docker cp place.dump daengs-place-db:/tmp/ && docker compose exec place-db pg_restore -U place -d place --clean --if-exists /tmp/place.dump

# ③ 서비스 기동 — 크롤러(worker·beat)는 일부러 목록에 없습니다 (코퍼스 정본은 로컬 서버)
docker compose -f docker-compose.yml -f docker-compose.gcp.yml --profile gait \
  up -d nginx backend place-search journey-service gait-analysis

# ④ 프론트
cd frontend && npm ci && npm run build && pm2 start ../ecosystem.config.js && pm2 save && pm2 startup
```

backend 는 아직 개발 모드(코드 마운트 + `uv run dev`)로 돕니다 — 검토 3단계(이미지
굽기)에서 바꾸기로 했고 지금은 감수합니다. 첫 기동은 uv sync + 모델 다운로드로 느립니다.

## Phase 2 — DNS + TLS (반나절)

- [ ] **인증서 발급** (표준 80 포트 인증이라 DNS 전환 후에 해야 합니다 — 순서 주의):
      가비아에서 두 서브도메인 A 레코드를 고정 IP 로 변경 → 전파 확인(`nslookup`) 후

  ```bash
  docker compose -f docker-compose.yml -f docker-compose.gcp.yml stop nginx
  docker run --rm -p 80:80 -v /srv/daengs/letsencrypt:/etc/letsencrypt certbot/certbot \
    certonly --standalone --agree-tos --no-eff-email -m <팀 이메일> \
    -d daengs.weareithero.cloud -d daengback.weareithero.cloud
  docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d nginx
  ```

- [ ] `nginx/gcp.conf` 의 `server_name`·인증서 경로가 실제 서브도메인과 일치하는지 확인
- [ ] 앱 담당자에게 `https://daengback.weareithero.cloud` 전달 (앱에 박히는 값 — IP 금지)

## Phase 3 — 검증 + 마무리 (반나절)

- [ ] 스모크 테스트:

  | 확인 | 기대 |
  | --- | --- |
  | `https://daengs.~/` | 프론트 렌더 + 자물쇠 |
  | `https://daengs.~/api/…` 로그인 | 200 (같은 오리진 쿠키 경로) |
  | `https://daengback.~/docs` | FastAPI 문서 |
  | `/ask` 질의 | 첫 요청은 예열로 느림 — 두 번째가 정상 속도 |
  | `/v2/places/search` · `/journey` | 200 |
  | `/screen/v1/screen` 사진 1장 | 200 (0.6~3초) |
  | `/gait/analyze` 영상 | 200 (분 단위 — 504 나면 타임아웃 설정 확인) |
  | 80 접속 | 443 리다이렉트 |
- [ ] VM 재부팅 후 자동 복구 확인 (`restart: unless-stopped` + `pm2 startup`)
- [ ] **디스크 스냅샷 1회** + 예산 알림 수신 확인
- [ ] 발표 2~3일 전 코드 프리즈. 배포는 수동: `git pull origin main` (완성 단위마다
      dev → main PR) → 의존성 바뀐 경우만 해당 컨테이너 재생성

## 운영 메모

- 인증서는 90일 — **9/21 이전에는 갱신이 아예 안 옵니다.** 이후 유지 시 60일쯤부터
  월 1회: nginx stop → 위 certbot 명령의 `certonly` 를 `renew` 로 → nginx up
- gait 는 `docker-compose.gcp.yml` 에서 `cpus: 2.5` 로 묶여 있습니다 — 분석 중에도
  다른 API 에 1.5 vCPU 가 남습니다

## 9/21 이후 — 종료 체크리스트

⚠ 계정이 일반 계정이라 **정지로는 과금이 계속됩니다**(디스크·미연결 고정 IP).
끝나면 삭제까지 가야 하고, 크레딧 만료(11-17) 뒤에는 말없이 실비 청구입니다.

- [ ] `pg_dump` 3개 최종 백업을 로컬로 회수 (`/srv/daengs/dumps`)
- [ ] 앱을 유지한다면: 가비아 A 레코드를 집 서버로 회귀 + 집에서 TLS 재구성 — 앱에는
      도메인이 박혀 있어 주소는 그대로 삽니다
- [ ] VM 삭제 → 디스크 삭제 확인 → **고정 IP 해제** → 스냅샷 정리 → 예산 화면에서 ₩0 확인

## 2차 (예고 — GCP 유지 확정 시)

크롤러·코퍼스·적재 이전. `DAENGS_CORPUS_DIR` 정본 컷오버(+로컬 워커 정지 같은 날),
crawler-worker·beat 기동, 증분 적재는 VM 에서 CPU 로(`rag load`), 전체 재적재만 스팟
GPU 또는 개발 PC. celery → Cloud Scheduler 최적화 후보와 같은 묶음으로.
