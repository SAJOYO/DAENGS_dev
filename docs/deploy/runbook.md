# GCP 이관 절차서 (runbook)

**명령어 수준 절차서**입니다. 무엇을/왜/일정은 [roadmap.md](roadmap.md) 가 정본이고
여기 다시 적지 않습니다. GCP 전용 구성은 `docker-compose.gcp.yml` + `nginx/gcp.conf` (D-042).

> ⚠ 서브도메인은 `daengs.weareithero.cloud` / `daengback.weareithero.cloud` 로 가정하고
> 씁니다. **가비아 실제 값 확인 후** 다르면 `nginx/gcp.conf` 의 `server_name`·인증서
> 경로와 이 문서의 도메인을 함께 고치세요.

## 0. 사전 (콘솔 — roadmap Phase 0)

예산 알림 50/80/100% · 고정 IP 예약(asia-northeast3) · VM e2-standard-4 / Ubuntu 24.04 LTS /
100GB pd-balanced · 방화벽 인바운드 **80·443 만 전체 공개**, SSH(22)는 IAP 또는 내 IP.
**5432 · 6379 · 8000 은 열지 않습니다** — 8000 은 호스트 내부(Next 서버사이드 프록시)용.

## 1. VM 셋업

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER        # 재로그인 필요

curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs
sudo npm i -g pm2

# 배포 체크아웃 밖 상태 폴더 (로컬 서버의 C:/deploy 와 같은 취급)
sudo mkdir -p /srv/daengs/{models/release,gait/release,letsencrypt,dumps,corpus-unused}
sudo chown -R $USER /srv/daengs

git clone -b main https://github.com/SAJOYO/DAENGS_dev.git ~/daengs
```

## 2. 상태 이전

로컬에서 만들어 `gcloud compute scp <파일> <VM>:/srv/daengs/dumps/` 로 올립니다.

### DB 덤프 2개 + 롤 (로컬 서버에서)

⚠ 먼저 서버 DB 에 `db/migrations/` 최근분(특히 `2026-09-01_training_rag_into_vectordb.sql`)이
적용됐는지 확인하고 뜨세요. vectordb 덤프에 Training RAG 테이블(`training_rag_*`)이 같이
담깁니다 (#112 — 별도 DB 아님).

```powershell
# PowerShell 의 > 는 바이너리를 깨뜨리므로 컨테이너 안에 만들고 docker cp 로 꺼냅니다
docker compose exec pgvector sh -c "pg_dumpall -U postgres --globals-only > /tmp/globals.sql"
docker compose exec pgvector sh -c "pg_dump -U postgres -Fc vectordb > /tmp/vectordb.dump"
docker cp pgvector:/tmp/globals.sql .
docker cp pgvector:/tmp/vectordb.dump .
docker compose exec place-db sh -c "pg_dump -U place -Fc place > /tmp/place.dump"
docker cp daengs-place-db:/tmp/place.dump .
```

`globals.sql` 을 뜨는 이유 — `pg_dump` 는 **롤을 담지 않습니다.** 서버의 `daengs` 롤은
손으로 만든 것이라 이것 없이 복원하면 소유자가 없어 실패합니다.

### 파일

| 무엇 | 어디로 | 비고 |
| --- | --- | --- |
| 스크리닝 가중치 2개 | `/srv/daengs/models/release/` | git 에 없음 (100MB 리밋) |
| gait `best.pt` · `yolov8n.pt` | `/srv/daengs/gait/release/` | git 에 없음 |
| 최상단 `.env` | `~/daengs/.env` | 아래 수정표 |
| `backend/.env` | `~/daengs/backend/.env` | 암호화 키 3개는 **로컬과 같은 값** — 새로 만들면 덤프해 온 암호문을 못 엽니다 |

최상단 `.env` 수정표:

| 항목 | GCP 값 |
| --- | --- |
| `SCREENING_RELEASE_DIR` | `/srv/daengs/models/release` |
| `GAIT_RELEASE_DIR` | `/srv/daengs/gait/release` |
| `DAENGS_CORPUS_DIR` | `/srv/daengs/corpus-unused` — **더미.** 크롤러를 안 띄워도 compose 가 파일 해석 시점에 `:?` 가드를 평가합니다 |

`backend/.env` 수정표:

| 항목 | GCP 값 |
| --- | --- |
| `DAENGS_CORS_ORIGINS` | `https://daengs.weareithero.cloud` (프론트 도메인 — Phase 2 에서) |

임베딩 모델(hf-cache 1.2GB)은 옮기지 않습니다 — 첫 기동 때 자동 다운로드.
`EMBEDDING_MODEL_KEY` 는 바꾸지 마세요 (코퍼스와 어긋나면 차원이 같아 조용히 틀립니다).

## 3. 복원 + 기동

```bash
cd ~/daengs
# ① DB 만 먼저 — 빈 볼륨이라 db/init(vectordb) · Alembic(place) 이 스키마를 만듭니다
docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d pgvector place-db

# ② 롤 → 데이터 (--clean 이 init 스키마를 덤프 것으로 갈아 끼웁니다)
docker cp /srv/daengs/dumps/globals.sql pgvector:/tmp/
docker compose exec pgvector psql -U postgres -f /tmp/globals.sql
docker cp /srv/daengs/dumps/vectordb.dump pgvector:/tmp/
docker compose exec pgvector pg_restore -U postgres -d vectordb --clean --if-exists /tmp/vectordb.dump
docker cp /srv/daengs/dumps/place.dump daengs-place-db:/tmp/
docker compose exec place-db pg_restore -U place -d place --clean --if-exists /tmp/place.dump

# ③ 서비스 — 크롤러(worker·beat)는 일부러 목록에 없습니다 (코퍼스 정본은 로컬 서버)
docker compose -f docker-compose.yml -f docker-compose.gcp.yml --profile gait \
  up -d nginx backend place-search journey-service gait-analysis

# ④ 프론트 (PM2, 로컬 서버와 같은 모양)
cd frontend && npm ci && npm run build
pm2 start ../ecosystem.config.js && pm2 save && pm2 startup
```

backend 는 아직 개발 모드(소스 마운트 + `uv sync` 후 기동)입니다 — 이미지 굽기는 2차
(roadmap §7). 첫 기동은 의존성 동기화 + 모델 다운로드로 느립니다.

## 4. DNS + TLS

인증서는 80 포트 인증(standalone)이라 **DNS 전환이 먼저**입니다.

1. 가비아: 두 서브도메인 A 레코드 → 고정 IP (TTL 은 Phase 0 에서 미리 300초로)
2. `nslookup daengback.weareithero.cloud` 로 전파 확인 후:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp.yml stop nginx
docker run --rm -p 80:80 -v /srv/daengs/letsencrypt:/etc/letsencrypt certbot/certbot \
  certonly --standalone --agree-tos --no-eff-email -m <팀 이메일> \
  -d daengs.weareithero.cloud -d daengback.weareithero.cloud
docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d nginx
```

3. `backend/.env` 의 `DAENGS_CORS_ORIGINS` 를 https 도메인으로 → `docker compose ... restart backend`
4. 앱 담당자에게 `https://daengback.weareithero.cloud` 전달 (앱에 박히는 값 — IP 금지)

## 5. 스모크 (완료 기준은 roadmap §6)

```bash
curl -sI https://daengs.weareithero.cloud/            # 200, 자물쇠
curl -sI http://daengs.weareithero.cloud/             # 301 → https
curl -s  https://daengback.weareithero.cloud/docs     # FastAPI 문서
```

`/ask` 는 첫 요청이 예열로 느립니다(두 번째가 정상). `/assistant/query` 는 인증 필수.
`/gait/analyze` 는 영상으로 분 단위 — 504 가 나면 `nginx/gcp.conf` 의 gait 타임아웃 확인.

## 6. 운영

- **수동 배포**: `git pull origin main` → 의존성이 바뀐 경우만 해당 컨테이너 재생성
- **인증서 갱신**: 90일 — 9/21 전에는 갱신이 없습니다. 유지 시 60일쯤부터 월 1회,
  위 발급 명령의 `certonly ...` 를 `renew` 로 바꿔 같은 순서(stop → renew → up)로
- **스냅샷**: Phase 3 에서 1회 + 유지 시 주기화 (2차)
- 종료(삭제/DNS 회귀)는 roadmap §8 체크리스트를 따릅니다 — **정지가 아니라 삭제까지**
