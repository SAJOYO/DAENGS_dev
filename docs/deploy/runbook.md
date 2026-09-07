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
sudo mkdir -p /srv/daengs/{models/release/gait-analysis,letsencrypt,dumps,corpus-unused}
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
| gait `best.pt` · `yolov8n.pt` | `/srv/daengs/models/release/gait-analysis/` | git 에 없음. 스크리닝 release 폴더의 하위입니다 — `GAIT_RELEASE_DIR` 과 같은 값이어야 합니다 |
| 최상단 `.env` | `~/daengs/.env` | 아래 수정표 |
| `backend/.env` | `~/daengs/backend/.env` | 암호화 키 3개는 **로컬과 같은 값** — 새로 만들면 덤프해 온 암호문을 못 엽니다 |

최상단 `.env` 수정표:

| 항목 | GCP 값 |
| --- | --- |
| `SCREENING_RELEASE_DIR` | `/srv/daengs/models/release` |
| `GAIT_RELEASE_DIR` | `/srv/daengs/models/release/gait-analysis` — 서버 관행대로 스크리닝 release 폴더의 하위입니다 |
| `DAENGS_CORPUS_DIR` | `/srv/daengs/corpus-unused` — **더미.** 크롤러를 안 띄워도 compose 가 파일 해석 시점에 `:?` 가드를 평가합니다 |
| `GAIT_STORAGE` · `GAIT_LOCAL_STORAGE_DIR` · `GAIT_BRIDGE_BASE_URL` | `local` · `/data/gait-bridge` · **`https://daengapi.weareithero.cloud`**. **D-052 로 이 볼륨이 정본입니다** — 임시가 아니고, GCS 로도 안 갑니다(#78 은 닫혔습니다). 가운데 값은 **컨테이너 안 경로**라 호스트에 그런 폴더는 없습니다(named volume `daengs_gait-bridge`). 마지막 값이 앱이 받는 `upload_url` 의 앞부분이라, `.env.example` 의 예시(`http://daengback.~`)를 그대로 두면 **앱이 집 서버로 영상을 올립니다.** ⚠️ 이 셋은 보행 전용이 아닙니다 — 피부진단·점령지 사진·프로필이 **같은 저장소**를 씁니다. 비우면 그 전부가 503 입니다 |
| `GEMINI_API_KEY` | backend/.env 의 값을 **루트에도** 넣습니다 — compose 의 `${GEMINI_API_KEY:-}` 는 루트 `.env` 에서 읽는데, 없으면 **빈 값이 env_file(backend/.env)을 덮어써서** `/life/ask`·라우터·Training RAG 생성이 전부 죽습니다 (2026-09-02 실제 확인) |

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
docker compose --profile gait up -d nginx backend place-search journey-service \
  gait-analysis gait-worker territory-vision-worker

# ③-1 점령 게임판 — 덤프에는 안 따라옵니다(옛 115u 세대). §6 "점령 게임판 적재 (GCP)"
#     를 여기서 한 번 밟으세요. 안 하면 지도에 점령지가 하나도 안 뜹니다

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

**경로별 기대 응답** — 배포 뒤 이 표대로 나오는지 봅니다. 인증이 필요한 경로는 **401 이
정답**이고, 404 가 나오면 그 엔드포인트가 아직 이 서버에 없다는 뜻입니다:

```bash
for p in /health /gait/records /app/gait/analyze /app/walks /journey /v2/places/search; do
  printf "%-24s %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' https://daengapi.weareithero.cloud$p)"
done
```

| 경로 | 기대 | 아니면 |
| --- | --- | --- |
| `/health` | 200 (`{"status":"ok","db":"ok"}`) | backend 기동 실패 |
| `/gait/records` | **410** — 옛 무인증 경로는 닫혀 있어야 합니다 (#145) | **400·200 이면 설정이 반영 안 된 것.** §6 의 inode 함정 |
| `/app/gait/analyze` (POST) | 401 | 404 면 새 계약이 안 올라온 것 |
| `/app/walks` (POST) | 401 | 〃 |
| `/journey` · `/v2/places/search` | 405 (GET 이라서) | 502 면 해당 컨테이너가 죽은 것 |

`/life/ask` 는 첫 요청이 예열로 느립니다(두 번째가 정상). `/assistant/query` 는 인증 필수.

`/app/gait/*` 의 영상 분석은 분 단위입니다 — 504 가 나면 `api-locations.inc` 의
`/app/gait/` 타임아웃(600s)이 실제로 반영됐는지부터 보세요 (§6 의 inode 함정).

## 6. 운영

- **배포 절차 (git push 배포 — §1).** dev → main 스냅샷 PR 이 머지된 상태에서.

  ⚠ **VM 의 `git pull` 이 곧 배포입니다.** backend 가 `fastapi dev`(reload)로 돌고
  `backend/src` 를 바인드 마운트해서, 워크트리가 바뀌는 순간 새 코드가 섭니다. 그래서
  **무엇이 바뀌는지 먼저 보고, DB 를 맞춘 뒤에** 워크트리를 옮깁니다.

  ```powershell
  # ① 개발 PC — main 을 VM 으로 push
  git switch main; git pull; git push gcp main
  ```
  ```bash
  # ② VM — 객체만 받는다 (워크트리는 아직 안 움직입니다)
  cd ~/daengs && git fetch
  git diff --stat HEAD origin/main
  ```
  ```bash
  # ③ db/migrations/ 에 새 파일이 있으면 **pull 보다 먼저** 적용한다
  git show origin/main:db/migrations/<파일>.sql \
    | docker compose exec -T pgvector psql -U daengs -d vectordb -v ON_ERROR_STOP=1
  ```

  ⚠ **`-v ON_ERROR_STOP=1` 을 빼지 마세요.** psql 기본값은 오류가 나도 다음 문장을 계속
  실행하고 **종료 코드 0** 을 냅니다. 여러 장을 반복문으로 돌리면 실패한 것을 못 알아채고
  ④ 로 넘어갑니다. 적용 뒤에는 같은 이름의 `verify_*.sql` 이 있으면 그것도 돌립니다.
  ```bash
  # ④ 워크트리 갱신 = 배포
  git merge --ff-only origin/main
  ```

  ③ 을 ④ 뒤로 미루면 **없는 테이블을 새 코드가 칩니다.** 리로드라 그 사이에 창이 없습니다.
  계정이 `-U daengs` 인 것도 잊기 쉽습니다 — 이 VM 의 수퍼유저는 `postgres` 가 아닙니다(§3).
  버전 테이블이 없어 **무엇을 적용했는지 DB 가 기억하지 않으니** 적용한 파일명은 사람이
  적어 둡니다. 로컬 서버와 GCP 는 **각각 적용**합니다 — 한쪽에 돌렸다고 다른
  쪽이 따라오지 않으므로 두 줄로 적어 둡니다 (roadmap §2-5).

  ④ 뒤, 바뀐 종류별 조치:
  - **백엔드 코드만** → 없음. ④ 로 끝입니다
  - **`uv.lock` · compose** → 영향받는 컨테이너 재생성:

    ```bash
    docker compose -f docker-compose.yml -f docker-compose.gcp.yml --profile gait \
      up -d --force-recreate backend place-search journey-service gait-analysis gait-worker \
      territory-vision-worker
    ```

    ⚠ **서비스 이름을 반드시 적습니다.** 인자 없이 `up -d` 하면 `crawler-worker`·
    `crawler-beat` 까지 뜹니다 — #65 로 크롤러 profile 이 없어졌기 때문입니다. worker 는
    로그 파일이 없으면 스스로 안 뜨지만 **beat 는 뜹니다.** 그러면 소비자 없는 큐에
    프리페치가 1분마다 쌓이고(하루 1,440개), 코퍼스 정본이 로컬 서버인데 GCP 가 크롤
    스케줄을 발사하게 됩니다.
    ⚠ **`gait-worker`** 는 D-043 으로 생긴 서비스입니다. 빠뜨리면 웹만 새 코드가 되고
    워커는 옛 코드로 남아, 증상이 "분석 결과만 옛날 것"으로 나옵니다.
    **`territory-vision-worker`** 도 같은 배포 단위입니다. 빠뜨리면 confirm은 성공하지만
    앱의 점령지 인증이 `VISION_PENDING`에서 끝나지 않습니다.
    ⚠ **트레이싱(D-054)을 켠 서버라면 `otel-collector` 도 목록에 넣고 `--profile tracing`
    을 함께 줍니다.** 프로파일만 붙이고 이름을 빼면 collector 가 안 뜨는데, 그때 backend
    는 아무 오류 없이 계속 돕니다 — 트레이스만 조용히 사라집니다. 반대로 이름만 넣고
    프로파일을 빼면 compose 가 "no such service" 로 멈추므로 그쪽은 안전합니다.
  - **nginx 설정만 (`nginx/gcp.conf` · `nginx/api-locations.inc`)** → **컨테이너를
    재생성합니다. `reload` 로는 반영되지 않습니다.**

    ```bash
    docker compose -f docker-compose.yml -f docker-compose.gcp.yml --profile gait \
      up -d --force-recreate nginx
    ```

    ⚠️ **`nginx -s reload` 를 쓰지 마세요. 오류 없이 아무 일도 안 일어납니다.**
    compose 가 설정을 **파일 하나씩** 마운트하는데, 파일 마운트는 경로가 아니라
    **inode 를 뭅니다.** `git merge`(또는 편집기 저장)는 새 파일을 만들어 이름을
    갈아끼우므로 새 inode 가 되고, 컨테이너의 마운트는 **옛 inode 를 계속 가리킵니다.**
    그래서 `nginx -t` 는 옛 파일을 검사해 **통과**하고 `reload` 는 옛 파일을 **다시
    읽습니다** — 로그에도 아무 문제가 안 보입니다.
    2026-09-02 배포에서 실제로 밟았습니다: `/gait/records` 가 410 이어야 하는데 400 이었고,
    호스트 파일에는 `return 410` 이 있는데 `docker compose exec nginx grep` 으로는 없었습니다.
    `--force-recreate nginx` 로 즉시 해결.

    ⚠️ `up -d` 를 **이름 없이** 부르면 nginx 는 compose 정의가 안 바뀌었으므로 그냥
    넘어갑니다. 서비스 이름을 찍고 `--force-recreate` 를 붙여야 합니다.

    ⚠️ 같은 함정이 **파일로 마운트하는 것 전부**에 있습니다 — `backend/pyproject.toml` ·
    `backend/uv.lock` · `backend/README.md`. 반대로 `backend/src` 는 **디렉터리** 마운트라
    해당 없습니다(그래서 코드 수정은 reload 로 반영됩니다).

    ⚠️ GCP 가 읽는 것은 `default.conf` 가 **아닙니다** — 오버레이가 `gcp.conf` 를 그 자리에
    끼웁니다. API 경로 블록은 `api-locations.inc` 한 곳에 있고 두 server 블록이 include
    합니다. dev 에서 `default.conf` 만 고친 변경은 GCP 에 **없는 것과 같습니다** (#150 · #145).
  - **프론트** → §3 ④ 의 빌드·배치를 반복하되 릴리스 폴더 이름을 새로(`-manual2`,
    `-manual3`…) 하고, 마지막을 `pm2 reload daengs-web` 로 (start 아님 — reload 가
    클러스터 무중단 교체입니다)
  - **`.env` 에 새 키가 생김** → `.env.example` 은 VM 에 따라오지 않습니다. §2 수정표를
    보고 손으로 넣으세요. 기본값이 없는 설정이면 backend 가 ④ 직후 안 뜹니다
- **코퍼스를 재적재했다면(`rag load`) — GCP 는 바뀌지 않습니다.** 개발 PC 는 로컬
  서버 DB 를 보고 두 DB 사이에 복제가 없습니다 (roadmap §2-5). 적재는 성공하고
  스모크도 통과하는데 앱에만 새 문서가 안 보입니다.
  🔴 **§2 의 덤프 → §3 ② 의 복원으로 하지 마세요.** 2026-09-07(#289)까지 이 문단이 그렇게
  적고 있었는데, **그 길은 운영 데이터를 지웁니다** — 아래 "Life 코퍼스만 동기화 (GCP)"
  를 따르세요. 구조적 해소는 roadmap §7-1
- **9/18 부터 main 프리즈** — 발표(9/21) 당일 무배포 (roadmap §4)
- **인증서 갱신**: 90일 — 9/21 전에는 갱신이 없습니다. 유지 시 60일쯤부터 월 1회,
  위 발급 명령의 `certonly ...` 를 `renew` 로 바꿔 같은 순서(stop → renew → up)로
- **스냅샷**: Phase 3 에서 1회 + 유지 시 주기화 (2차)
- 종료(삭제/DNS 회귀)는 roadmap §8 체크리스트를 따릅니다 — **정지가 아니라 삭제까지**

### Life 코퍼스만 동기화 (GCP)

**언제** — 개발 PC 에서 `rag load` 로 집 서버 코퍼스를 늘린 뒤, 그것을 GCP 에 반영할 때.
처음 돈 것은 2026-09-07 (#289, `documents` 8,990 → 9,838).

🔴 **`vectordb` 를 통째로 덤프·복원하면 안 됩니다.** `db/init/` 이 만드는 테이블 31개가
전부 이 한 DB 안이라, `documents` 옆에 `app_users` · `pets` · `walks` · `chat_*` ·
`territory_*` · `screening_records` 가 **같이 있습니다.** `pg_restore --clean` 은 GCP 의
**운영 사용자 데이터를 집 서버의 개발 데이터로 덮어씁니다.** §3 ② 의 복원은 **빈 볼륨을
세울 때의 절차**지 갱신 절차가 아닙니다.

**`documents` 한 테이블만 갈아 끼웁니다.** 그래도 되는 근거 셋:

- **`documents` 를 참조하는 FK 가 없습니다** — `TRUNCATE` 가 다른 테이블을 안 건드립니다.
- **서빙은 `documents` 에 쓰지 않습니다.** 쓰는 곳은 `rag load`(`stages/load.py`) 하나뿐이고
  개발 PC → 집 서버로만 돕니다. GCP 쪽은 **읽기 전용 사본**이라 갈아 끼워도 잃을 것이 없습니다.
- **`training_rag_*` 는 다른 코퍼스입니다** — 건드리지 마세요.

```powershell
# ① 집 서버에서 documents 만 뜬다 (사무실 PC 에서)
docker compose exec -T pgvector sh -c "pg_dump -U postgres -d vectordb -t public.documents --data-only --no-owner -f /tmp/documents.sql"
docker compose exec -T pgvector sh -c "psql -U postgres -d vectordb -tAc 'SELECT count(*) FROM documents'"
docker compose exec -T pgvector sh -c "gzip -f /tmp/documents.sql"
docker cp pgvector:/tmp/documents.sql.gz .\documents.sql.gz
```

컨테이너 안에 만들고 `docker cp` 로 꺼냅니다 — **PowerShell 의 `>` 로 직접 받지 마세요**(§2).
`-Fc`(커스텀)가 아니라 **평문**인 이유는 ③ 에서 `TRUNCATE` 와 한 트랜잭션으로 묶으려면
`psql -f` 여야 하기 때문입니다. 여기서 나온 **행 수를 적어 두세요.**

```bash
# ② VM — 되돌릴 것을 먼저 만든다
cd ~/daengs
docker compose exec -T pgvector psql -U daengs -d vectordb -tAc 'SELECT count(*) FROM documents'
docker compose exec -T pgvector sh -c "pg_dump -U daengs -d vectordb -t public.documents --data-only --no-owner -f /tmp/documents-before.sql && gzip -f /tmp/documents-before.sql"
docker cp pgvector:/tmp/documents-before.sql.gz ~/
```

§6 의 전체 백업과 **별개로 한 장 더** 뜹니다. ③ 이 실패했을 때 되돌릴 것이 전체 덤프뿐이면
운영 테이블까지 같이 되돌리게 되기 때문입니다.

```bash
# ③ VM — 한 트랜잭션으로 갈아 끼운다
gunzip -c ~/documents.sql.gz > /tmp/documents.sql
docker cp /tmp/documents.sql pgvector:/tmp/documents.sql
docker compose exec -T pgvector psql -U daengs -d vectordb \
  -v ON_ERROR_STOP=1 --single-transaction \
  -c 'TRUNCATE public.documents' -f /tmp/documents.sql
```

⚠️ **`--single-transaction` 을 빼지 마세요.** 이것이 있어야 `TRUNCATE` 와 적재가 한
트랜잭션이 되어, 적재가 깨지면 `TRUNCATE` 까지 되돌아갑니다. 빼면 **코퍼스가 빈 채로 남는
구간**이 생기고 그동안 `/life/ask` 가 전부 404(근거 0건)입니다.

`TRUNCATE` 권한은 **GCP 에서만** 됩니다 — 그쪽은 볼륨을 `POSTGRES_USER=daengs` 로 초기화해서
`daengs` 가 소유자입니다. 집 서버는 `documents` 소유자가 `postgres` 라 같은 명령이 안
먹습니다(거기선 뜨기만 하므로 상관없습니다).

```bash
# ④ 확인
docker compose exec -T pgvector psql -U daengs -d vectordb -c "
  SELECT count(*) FROM documents;
  SELECT category, count(*) FROM documents GROUP BY 1 ORDER BY 2 DESC;
  SELECT count(*) FROM documents WHERE metadata ? 'org';
  SELECT count(*) FROM documents WHERE content_tsv IS NOT NULL;
  SELECT vector_dims(embedding), count(*) FROM documents WHERE embedding IS NOT NULL GROUP BY 1;"
```

`content_tsv` 가 전체 행 수와 같고 벡터가 전부 1,024차원이면 된 것입니다.

#### 걸린 것 (2026-09-07, 처음 돌리며)

1. 🔴 **`content_tsv` 는 생성 컬럼이라 `COPY` 가 거부합니다.**
   `column "content_tsv" is a generated column / Generated columns cannot be used in COPY`.
   위 ① 처럼 **`pg_dump` 를 쓰면 자동으로 처리**되므로 안 만납니다. 컬럼 목록을 손으로
   짤 때만 나는데, 그때는 `information_schema.columns` 를 **`is_generated = 'NEVER'`** 로
   거르세요. 빼도 GCP 에서 같은 정의로 다시 계산됩니다(전체 행이 채워지는 것으로 확인).
2. ⚠️ **기대 행 수를 `docs/life/roadmap.md` 에서 가져올 때 무엇을 보는지 확인하세요.**
   그 문서는 **청크 수**(§1, 예: 10,304)와 **DB 문서 수**(§0, 예: 9,836)를 **다른 자리에
   다른 수로** 적습니다. 여기서 맞춰야 하는 것은 **`documents` 행 수**입니다.
   #289 는 이 둘을 섞어 "10,304 가 나와야 한다"고 적었다가 실제 9,838 에서 갸웃했습니다.
3. ⚠️ **개발 PC 에는 `pg_dump` 가 없을 수 있습니다.** 그때 Docker Desktop 이 꺼져 있으면
   컨테이너로 우회하는 길도 막힙니다. 집 서버 DB 는 LAN 에 열려 있으므로
   (`POSTGRES_IP`, CLAUDE.md) **개발 PC 에서 psycopg 로 붙어 `COPY … TO STDOUT`** 으로
   같은 일을 할 수 있습니다 — `backend` 의 `.venv` 에 psycopg 가 이미 있습니다.
   그 길로 갈 때만 1번의 생성 컬럼 문제를 만납니다.

### 점령 게임판 적재 (GCP)

**언제** — ⓐ GCP 를 처음 세울 때(§3 ③ 뒤) ⓑ 게임판 세대가 바뀔 때.
`.github/workflows/territory-sites-ingest.yml` 은 `runs-on: [self-hosted]` 라 **집 서버 place-db
에만** 적재합니다. GCP 는 그 러너가 아니므로 같은 일을 여기서 손으로 합니다.

**안 하면** — `GET /territory/sites/nearby` 가 **어디서 불러도 `{"sites": []}`** 입니다.
읽기 질의가 현행 세대만 거르는데(`daengs_place/territory/sites.py` 의
`site_id LIKE 'territory-site:hex-v1:140:%'`), 09-02 덤프에 있는 건 옛 `anchor-hex:115:q:r`
행이고 Alembic `0021` 이 그것을 `territory-site:hex-v1:115:q:r` 로만 바꾸기 때문입니다.
**옛 행이 몇 개든 결과는 같습니다** — 격자가 다르면 같은 id 가 다른 자리를 뜻하므로
`0021` 이 일부러 세대를 안 올립니다. 앱은 이 API 를 부르므로 증상은 "지도에 점령지가
하나도 없다" 입니다.

⚠️ **아래 네 값은 워크플로우의 `env:` 와 같아야 합니다.** 세대를 바꾸면 두 군데를 같이
고치세요 — 워크플로우가 `env:` 로만 읽을 수 있어 한 곳으로 못 모았습니다.

```bash
cd ~/daengs
TAG=territory-sites-140u-20260903
ASSET=territory-lamps-140u-20260903.ndjson.gz
SHA256=dacb49e4c4f9969ff1efdd1ced58527b5b4c2768c785f902538aeed1f2224e1f
EXPECTED=362309

# ① 공개 릴리스에서 받고 해시를 대조합니다. 여기서 멈추면 그 뒤로 가지 마세요
#    ⚠ `cd /tmp` 하지 마세요 — 아래 `docker compose exec` 는 compose 파일이 있는
#      ~/daengs 에서 불러야 합니다. 받는 것만 /tmp 로 보냅니다
curl -fL -o "/tmp/$ASSET" \
  "https://github.com/rkbuhtig/DAENGS_geo/releases/download/$TAG/$ASSET"
( cd /tmp && echo "$SHA256  $ASSET" | sha256sum -c - )

# ② 풀어서 place-search 컨테이너로. 적재는 DB 접속을 가진 그 컨테이너 안에서 돕니다
gunzip -kf "/tmp/$ASSET"
docker cp "/tmp/${ASSET%.gz}" daengs-place-search:/tmp/territory-lamps.ndjson

# ③ dry-run 먼저 — 받다 만 파일을 실적재 전에 잡습니다
#    (모듈에 MIN_PRODUCTION_SITES=300000 가드가 있고 --expected-sites 와 함께 봅니다)
docker compose exec -T place-search uv run --no-sync \
  python -m daengs_place.ingest.territory_sites /tmp/territory-lamps.ndjson \
  --expected-sites "$EXPECTED" --dry-run

# ④ 실적재. source='lamp' 를 통째로 DELETE 하고 다시 넣는 원자적 교체라
#    여러 번 돌려도 안전하고, 옛 115u 행도 이때 같이 사라집니다
#    (옛 anchor 행의 source 도 'lamp' 입니다 — Alembic 0012 의 원본 SQL)
docker compose exec -T place-search uv run --no-sync \
  python -m daengs_place.ingest.territory_sites /tmp/territory-lamps.ndjson \
  --expected-sites "$EXPECTED"

# ⑤ 뒷정리
docker compose exec -T place-search rm -f /tmp/territory-lamps.ndjson
rm -f "/tmp/$ASSET" "/tmp/${ASSET%.gz}"
```

**검증 — 셋 다 봅니다.** 워크플로우가 자기 자리에서 하는 것과 같습니다. 한쪽만 헐거우면
GCP 에서만 조용히 틀립니다:

```bash
# 현행 세대 건수 = EXPECTED
docker compose exec -T place-db psql -v ON_ERROR_STOP=1 -U place -d place -Atc \
  "SELECT count(*) FROM territory_site WHERE source = 'lamp' AND site_id LIKE 'territory-site:hex-v1:140:%';"

# 옛 세대 잔존 = 0
docker compose exec -T place-db psql -v ON_ERROR_STOP=1 -U place -d place -Atc \
  "SELECT count(*) FROM territory_site WHERE source = 'lamp' AND site_id NOT LIKE 'territory-site:hex-v1:140:%';"

# 공개 API 로도 보이는지 (서울시청 반경 3km)
curl -s 'https://daengapi.weareithero.cloud/territory/sites/nearby?lat=37.5665&lng=126.9780&radius_m=3000&limit=1'
```

- **`/v2/places/search` 와 같은 DB 입니다.** 적재가 place-db 를 무겁게 쓰는 동안 시설 검색이
  느려질 수 있어 트래픽이 없는 시간에 돌립니다 (워크플로우가 시설 적재와 `concurrency:
  place-data-sync` 로 직렬화하는 것과 같은 이유입니다).
- **집 서버는 이 절차를 쓰지 않습니다** — 거기는 워크플로우를 수동 실행(`workflow_dispatch`)
  하면 됩니다. 로컬 서버와 GCP 는 **각각** 적재입니다 (roadmap §2-5 와 같은 규칙).
- 워크플로우로 자동화하려면 GCP 를 러너로 등록하거나 SSH 배포 액션이 필요합니다 —
  roadmap §7 의 CI/CD 항목과 같은 자리라 그때 같이 봅니다.
