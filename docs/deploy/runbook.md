# GCP 이관 절차서 (runbook)

**명령어 수준 절차서**입니다. 무엇을/왜/일정은 [roadmap.md](roadmap.md) 가 정본이고
여기 다시 적지 않습니다. GCP 전용 구성은 `docker-compose.gcp.yml` + `nginx/gcp.conf` (D-042).

> 서브도메인은 팀 결정(2026-09-01)으로 **클라우드 전용 새 이름**입니다 —
> `daengapp.weareithero.cloud`(프런트) / `daengapi.weareithero.cloud`(백엔드).
> 기존 `daengs`·`daengback` 은 로컬(개발) 서버가 그대로 쓰므로 **가비아 기존 레코드는
> 건드리지 않습니다.** 덕분에 DNS 컷오버가 없습니다 — 새 레코드는 고정 IP 예약 직후
> 바로 만들 수 있습니다(아직 아무도 안 쓰는 이름이라 전파를 기다릴 일이 없습니다).

## 0. 사전 (콘솔 — roadmap Phase 0)

예산 알림 50/80/100% · 고정 IP 예약(asia-northeast3) · VM e2-standard-4 / Ubuntu 24.04 LTS /
100GB pd-balanced · 방화벽 인바운드 **80·443 만 전체 공개**, SSH(22)는 IAP 또는 내 IP.
**5432 · 6379 · 8000 은 열지 않습니다** — 8000 은 호스트 내부(Next 서버사이드 프록시)용.

고정 IP 가 나오면 **가비아에 새 A 레코드 2개를 바로 추가**합니다 (기존 레코드는 안 건드림):
`daengapp` → 고정 IP · `daengapi` → 고정 IP, TTL 300.

## 1. VM 셋업

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER        # 재로그인 필요

curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs
sudo npm i -g pm2

# 배포 체크아웃 밖 상태 폴더 (로컬 서버의 C:/deploy 와 같은 취급)
sudo mkdir -p /srv/daengs/{models/release,gait/release,letsencrypt,dumps,corpus-unused}
sudo chown -R $USER /srv/daengs

# 저장소는 GitHub 에서 직접 clone 하지 않습니다 — 아래 "git push 배포" 참고
git init --bare --initial-branch=main ~/repo.git
```

**git push 배포** — 조직 정책으로 이 레포의 deploy key 가 비활성이고, 사용자 전체 권한
토큰을 공개 서버에 두는 것은 피하려고, GitHub 를 거치지 않고 **개발 PC 가 VM 으로
직접 push** 합니다 (GitHub 자격증명이 VM 에 없습니다):

```powershell
# 개발 PC 에서 1회 설정
git remote add gcp ssh://daengs@34.64.233.102/home/daengs/repo.git
# $env:GIT_SSH_COMMAND = "ssh -i C:/Users/<사용자>/.ssh/google_compute_engine"

# 최초 배포
git switch main; git pull; git push gcp main
```

```bash
# VM 에서 1회: bare 저장소에서 작업 트리 생성
git clone -b main ~/repo.git ~/daengs
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
| `GAIT_RELEASE_DIR` | `/srv/daengs/models/release/gait-analysis` — 서버 관행대로 스크리닝 release 폴더의 하위입니다 |
| `DAENGS_CORPUS_DIR` | `/srv/daengs/corpus-unused` — **더미.** 크롤러를 안 띄워도 compose 가 파일 해석 시점에 `:?` 가드를 평가합니다 |
| `GEMINI_API_KEY` | backend/.env 의 값을 **루트에도** 넣습니다 — compose 의 `${GEMINI_API_KEY:-}` 는 루트 `.env` 에서 읽는데, 없으면 **빈 값이 env_file(backend/.env)을 덮어써서** `/ask`·라우터·Training RAG 생성이 전부 죽습니다 (2026-09-02 실제 확인) |

`backend/.env` 수정표:

| 항목 | GCP 값 |
| --- | --- |
| `DAENGS_CORS_ORIGINS` | `https://daengapp.weareithero.cloud` (프론트 도메인 — Phase 2 에서) |

임베딩 모델(hf-cache 1.2GB)은 옮기지 않습니다 — 첫 기동 때 자동 다운로드.
`EMBEDDING_MODEL_KEY` 는 바꾸지 마세요 (코퍼스와 어긋나면 차원이 같아 조용히 틀립니다).

## 3. 복원 + 기동

```bash
cd ~/daengs
# ① DB 만 먼저 — 빈 볼륨이라 db/init(vectordb) · Alembic(place) 이 스키마를 만듭니다
docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d pgvector place-db

# ② 롤 → 데이터 (--clean 이 init 스키마를 덤프 것으로 갈아 끼웁니다)
# ⚠ 서버와 달리 GCP 의 수퍼유저는 postgres 가 아니라 daengs 입니다 — 빈 볼륨을
#   POSTGRES_USER=daengs 로 초기화했기 때문. 그래서 전부 -U daengs 로 부릅니다.
#   globals.sql 의 "ALTER ROLE daengs ... NOSUPERUSER" 가 permission denied 로
#   실패하는 것은 **의도된 결과**입니다 — GCP 에선 daengs 가 관리자로 남아야 합니다.
docker cp /srv/daengs/dumps/globals.sql pgvector:/tmp/
docker compose exec pgvector psql -U daengs -d vectordb -f /tmp/globals.sql
docker cp /srv/daengs/dumps/vectordb.dump pgvector:/tmp/
docker compose exec pgvector pg_restore -U daengs -d vectordb --clean --if-exists /tmp/vectordb.dump
docker cp /srv/daengs/dumps/place.dump daengs-place-db:/tmp/
docker compose exec place-db pg_restore -U place -d place --clean --if-exists /tmp/place.dump

# ③ 서비스 — 크롤러(worker·beat)는 일부러 목록에 없습니다 (코퍼스 정본은 로컬 서버)
# ⚠ 여기서는 gcp 오버레이를 **얹지 않습니다.** gcp.conf 는 인증서 파일을 참조하는데
#   §4 전에는 인증서가 없어 nginx 가 뜨자마자 죽습니다. Phase 1 은 기본 설정(80/8000)
#   으로 올리고, §4 발급 후에 gcp 오버레이로 nginx 만 재생성합니다.
docker compose --profile gait up -d nginx backend place-search journey-service gait-analysis

# ④ 프론트 — deploy.yml 의 standalone 배치(releases/<해시>/ + current 링크)를
#   /srv/daengs/web 에 재현하고 PM2 를 systemd 에 등록합니다
cd frontend && npm ci && npm run build
SHA=$(git -C ~/daengs rev-parse --short HEAD)
REL=/srv/daengs/web/releases/${SHA}-manual1
mkdir -p "$REL" "$REL/.next"
cp -r .next/standalone/. "$REL/"; cp -r .next/static "$REL/.next/static"; cp -r public "$REL/public"
ln -sfn "$REL" /srv/daengs/web/current
DAENGS_DEPLOY_ROOT=/srv/daengs/web pm2 start ~/daengs/ecosystem.config.js && pm2 save
sudo env PATH=/usr/local/bin:/usr/bin:/bin pm2 startup systemd -u daengs --hp /home/daengs
```

backend 는 아직 개발 모드(소스 마운트 + `uv sync` 후 기동)입니다 — 이미지 굽기는 2차
(roadmap §7). 첫 기동은 의존성 동기화 + 모델 다운로드로 느립니다.

## 4. TLS

인증서는 80 포트 인증(standalone)이라 **A 레코드(§0)가 먼저 있어야** 합니다.
`nslookup daengapi.weareithero.cloud` 가 고정 IP 를 돌려주는지 확인 후:

```bash
docker compose stop nginx
docker run --rm -p 80:80 -v /srv/daengs/letsencrypt:/etc/letsencrypt certbot/certbot \
  certonly --standalone --agree-tos --register-unsafely-without-email -n \
  -d daengapp.weareithero.cloud -d daengapi.weareithero.cloud
# 발급 후에야 gcp 오버레이(443, gcp.conf)로 nginx 를 재생성합니다.
# gait 도 같이 — 오버레이의 cpus 제한이 이때 적용됩니다.
docker compose -f docker-compose.yml -f docker-compose.gcp.yml --profile gait up -d nginx gait-analysis
```

- 이메일 없이 등록하는 이유: Let's Encrypt 는 만료 안내 메일 서비스를 종료했고(2025-06),
  갱신은 어차피 §6 대로 수동입니다.
- ⚠ `-d` 순서를 지키세요 — **첫 번째(daengapp)가 인증서 폴더 이름**이 되고,
  `nginx/gcp.conf` 의 경로가 그 이름을 가리킵니다.
- `backend/.env` 의 `DAENGS_CORS_ORIGINS` 가 https 프론트 도메인인지 확인
  (§2 에서 미리 넣었으면 조치 없음)
- 앱 담당자에게 `https://daengapi.weareithero.cloud` 전달 (앱에 박히는 값 — IP 금지)

## 5. 스모크 (완료 기준은 roadmap §6)

```bash
curl -sI https://daengapp.weareithero.cloud/           # 200, 자물쇠
curl -sI http://daengapp.weareithero.cloud/            # 301 → https
curl -s  https://daengapi.weareithero.cloud/docs       # FastAPI 문서
```

`/ask` 는 첫 요청이 예열로 느립니다(두 번째가 정상). `/assistant/query` 는 인증 필수.
`/gait/analyze` 는 영상으로 분 단위 — 504 가 나면 `nginx/gcp.conf` 의 gait 타임아웃 확인.

## 6. 운영

- **배포 절차 (git push 배포 — §1).** dev → main 스냅샷 PR 이 머지된 상태에서:

  ```powershell
  # ① 개발 PC — main 을 VM 으로 push
  git switch main; git pull; git push gcp main
  ```
  ```bash
  # ② VM — 체크아웃 갱신
  git -C ~/daengs pull
  ```

  여기서 갈립니다:
  - **백엔드 코드만 바뀜** → 조치 없음. 컨테이너가 `backend/src` 를 마운트한 개발
    모드(reload)라 pull 만으로 자동 반영됩니다.
  - **백엔드 의존성(`uv.lock`) 바뀜** → 영향받는 컨테이너 재생성:
    `docker compose -f docker-compose.yml -f docker-compose.gcp.yml --profile gait up -d --force-recreate backend place-search journey-service gait-analysis`
  - **프론트 바뀜** → §3 ④ 의 빌드·배치를 반복하되 릴리스 폴더 이름을 새로
    (`-manual2`, `-manual3`…) 하고, 마지막을 `pm2 reload daengs-web` 로 (start 아님 —
    reload 가 클러스터 무중단 교체입니다).
  - **compose·nginx 설정 바뀜** → `docker compose -f docker-compose.yml -f docker-compose.gcp.yml --profile gait up -d` (바뀐 것만 재생성됨)
  - **`db/migrations/` 추가됨** → 해당 SQL 을 pgvector 컨테이너에서 `-U daengs` 로 수동 실행.
    버전 테이블이 없어 **무엇이 적용됐는지 DB 가 기억하지 않습니다** — 로컬 서버와 GCP 에
    각각 적용해야 하므로 어디까지 했는지는 사람이 기억합니다 (roadmap §2-5)
  - **코퍼스를 재적재함(`rag load`)** → **GCP 는 바뀌지 않습니다.** 개발 PC 는 로컬 서버 DB 를
    보고 두 DB 사이에 복제가 없습니다 (roadmap §2-5). 반영하려면 §2 의 덤프를 다시 뜨고
    §3 ② 의 복원을 다시 돌립니다 — 아직 한 번도 해 본 적이 없어 전용 절차는 쓰지 않았습니다.
    처음 돌릴 때 걸린 것을 여기에 적으세요
- **9/18 부터 main 프리즈** — 발표(9/21) 당일 무배포 (roadmap §4)
- **인증서 갱신**: 90일 — 9/21 전에는 갱신이 없습니다. 유지 시 60일쯤부터 월 1회,
  위 발급 명령의 `certonly ...` 를 `renew` 로 바꿔 같은 순서(stop → renew → up)로
- **스냅샷**: Phase 3 에서 1회 + 유지 시 주기화 (2차)
- 종료(삭제/DNS 회귀)는 roadmap §8 체크리스트를 따릅니다 — **정지가 아니라 삭제까지**
