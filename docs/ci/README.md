# 여기 있는 워크플로는 **안 돕니다** — 왜 뺐고, 무엇이 대신하나

GitHub Actions 는 `.github/workflows/` 아래만 읽습니다. 이 폴더의 `.yml` 일곱은
**한때 모든 PR 에서 돌던 것**이고, 2026-09-10 에 여기로 옮겼습니다.

**지우지 않고 옮긴 이유** — 되살릴 때 `git mv` 한 번이면 끝나게 하려고, 그리고
*"이런 검사가 있었다"* 를 다음 사람이 git 로그를 뒤지지 않고 보게 하려고입니다.
파일 안의 주석에 각 검사가 **왜** 생겼는지가 적혀 있는데(대부분 실제로 한 번 데인
자리입니다), 지우면 그 맥락이 통째로 사라집니다.

## 왜 뺐나

**Actions 무료 한도가 소진됐습니다** (2026-09-09). 그 뒤로 이 일곱이 **2~3초 만에
전부 실패**합니다. 실패 이유가 잡 로그가 아니라 annotation 에만 뜨기 때문에,
**내용이 틀린 건지 한도 때문인지 화면으로 구별할 수 없습니다.**

그러면 검사가 하는 일이 뒤집힙니다 — 빨간 X 가 정보가 아니라 **소음**이 되고,
진짜 실패가 그 사이에 섞여도 아무도 안 봅니다. `RAG-085` 가 상태 화면을 두고 적은
*"이미 노랑이라 진짜 문제가 안 보인다 — 지표가 죽는다"* 와 같은 모양입니다.

⚠ **과금을 늘리지 않기로 했습니다.** 그래서 "기다리면 풀린다"가 아니라 **판단**입니다.

## 무엇이 대신하나 — 머지 전 로컬 게이트

**`dev` 머지가 곧 배포**라 이제 이것이 마지막 관문입니다. 전부 사람(과 agent)이 돌립니다.

| | 명령 | 무엇을 잡나 |
| --- | --- | --- |
| 1 | `cd backend && uv run check` | 마이그레이션 이름·짝·단언 등록, `db-migrate.yml` 의 Windows 바이트. **3초** |
| 2 | `cd backend && uv run pytest` | 전체. 약 9~15분 |
| 3 | `cd frontend && npm run lint` | 프론트를 건드렸으면 |
| 4 | `docker compose config --quiet` | compose 가 렌더되나 |
| 5 | `docker run --rm -v "$PWD/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.31.1 nginx -t` | nginx 문법 |
| 6 | `cd backend && uv run ruff check src/daengs_journey tests/journey` (place 도 같은 모양) | 패키지별 린트 |
| 7 | 아래 「버리는 DB 가 필요한 검사 둘」 | pytest 가 **조용히 건너뛰는** 것들 |

1·2·3 은 `CLAUDE.md` 의 규칙 그대로입니다. 4~7 이 여기로 옮겨 온 몫입니다.

## 🔴 버리는 DB 가 필요한 검사 둘 — 이것만은 꼭 읽으세요

**`uv run pytest` 는 이것들을 조용히 건너뜁니다.** 초록으로 통과하고, 안 돌았다는
말은 아무 데도 안 나옵니다. 2026-09-10 실측:

```
15 skipped in 1.18s
SKIPPED  WALK_PIN_TEST_DATABASE_URL: disposable local DB not configured
SKIPPED  set LIVE_STORYBOARD_TEST_DSN to a disposable loopback PostgreSQL
```

**서버 DB 가 켜져 있어도 skip 됩니다.** 이 테스트들은 공유 DB 가 아니라 **버리는
Postgres** 를 요구합니다 — 스키마를 만들고 부수기 때문입니다. 팀에 하나뿐인 운영
DB 를 겨누면 안 됩니다.

### ① Walk 저장 검사 (15건)

```bash
docker run -d --rm --name daengs-ci-pg -p 55432:5432 \
  -e POSTGRES_PASSWORD=test-password -e POSTGRES_DB=walk_pin_test postgres:17

cd backend
WALK_PIN_TEST_DATABASE_URL="postgresql+asyncpg://postgres:test-password@127.0.0.1:55432/walk_pin_test" \
LIVE_STORYBOARD_TEST_DSN="postgresql://postgres:test-password@127.0.0.1:55432/walk_pin_test" \
uv run pytest -q -rs tests/walk/entries/test_walk_entry_v2_db.py \
  tests/walk/entries/test_walk_entry_v2.py tests/walk/photos/test_walk_photo_db.py \
  tests/walk/diary/test_diary_generation_db.py tests/walk/storyboard/test_walk_storyboard_db.py

docker rm -f daengs-ci-pg
```

⚠ **`-rs` 를 꼭 붙이세요.** skip 이 한 건이라도 보이면 **안 돈 것**입니다.
`walk-entry-v2-tests.yml` 의 `Require executed checks` 단계가 하던 일이 그것입니다.
포트를 `55432` 로 둔 것은 운영 `5432` 와 안 겹치게 하려고입니다.

### ② 마이그레이션 변조 하네스 (330건)

`verify_*.sql` 이 **틀린 상태를 실제로 잡아내는지**를 봅니다. 마이그레이션을 버리는
스키마에 적용한 뒤, verify 가 실패해야 마땅한 상태를 일부러 만들어 놓고 *"이때
진짜 실패하는가"* 를 확인합니다. **`verify_` 가 SELECT 나열이라 틀려도 녹색이던
부채가 두 번 났기 때문에**(#273 · #292) 생긴 검사입니다.

```bash
docker run -d --rm --name daengs-ci-pgvector -p 55432:5432 \
  -e POSTGRES_PASSWORD=test-password -e POSTGRES_DB=migration_test \
  pgvector/pgvector:pg17

PGHOST=127.0.0.1 PGPORT=55432 PGUSER=postgres PGPASSWORD=test-password \
PGDATABASE=migration_test python tools/check_migration_verification.py sql

docker rm -f daengs-ci-pgvector
```

⚠ **`postgres:17` 이 아니라 `pgvector/pgvector:pg17`** 입니다. 마이그레이션 하나가
`vector` 컬럼과 `hnsw` 인덱스를 만들어서, 확장 없는 Postgres 에서는 아예 안 돕니다.
⚠ 코드 첫 줄이 `PGHOST` 가 로컬인지 **단언**합니다 — 일부러 데이터를 망가뜨리는
검사라 운영 DB 를 겨누면 안 됩니다. `psql` 이 없으면 컨테이너 안의 것을 쓰세요.

**⚠ `db/` 를 건드리는 PR 이면 이것을 돌리세요.** 지금 이 검사가 도는 곳이
어디에도 없습니다.

## 파일별 — 무엇을 하던 것인가

| 파일 | 하던 일 | 로컬에서 대신하는 것 |
| --- | --- | --- |
| `backend-tests.yml` | **모든 PR 에서 `uv run pytest` 전체.** `paths` 필터가 없는 것이 의도였습니다 — 필터에 안 걸려 검사가 안 돌던 것이 이 파일이 생긴 이유입니다 (#230) | 위 2 |
| `migration-verification-tests.yml` | 잡 셋. `names`(이름·짝) · `windows`(`db-migrate.yml` 의 cp949 바이트) · **`postgres`(변조 하네스)** | 앞의 둘은 **`uv run check` 가 같은 스크립트를 부릅니다.** 셋째만 위 ② |
| `journey-tests.yml` | journey pytest + compose 렌더 + `nginx -t` + 패키지 ruff | 위 4·5·6 |
| `place-search-tests.yml` | place 쪽 같은 구성 | 위 4·5·6 |
| `territory-ownership-tests.yml` | Postgres 를 띄워 Territory 소유권 DB 테스트 | 위 ① 과 같은 모양 (DSN 은 파일 참고) |
| `walk-entry-context-tests.yml` | 같음 — Walk 엔트리 컨텍스트 | 〃 |
| `walk-entry-v2-tests.yml` | 같음 — 핀·사진·일기·스토리보드 저장 + **skip 이면 실패** | 위 ① |

## 되살리려면

```bash
git mv docs/ci/<이름>.yml .github/workflows/<이름>.yml
```

그것뿐입니다. 파일 내용은 옮길 때 **한 글자도 안 고쳤습니다** — 되살렸을 때
그대로 돌아야 하고, 손대면 "왜 그렇게 짰나"가 적힌 주석과 어긋납니다.

**되살릴 만한 조건**: 한도가 회복되거나 유료로 전환할 때, 또는 self-hosted 러너를
CI 전용으로 하나 더 붙일 때입니다. ⚠ 지금 있는 self-hosted 러너는 **배포 러너**라
거기로 옮기면 안 됩니다 — compose 를 띄우는 잡이 **운영 컨테이너와 이름·포트가
겹치고**, DB 잡은 팀에 하나뿐인 운영 DB 를 겨눕니다. 서버가 꺼져 있으면 큐에
쌓이는 것도 실제로 겪었습니다 (2026-09-10).

## 여기 없는 것 — `.github/workflows/` 에 남아 있는 다섯

전부 **self-hosted** 라 Actions 한도와 무관하고, 지금도 정상으로 돕니다.

| 파일 | |
| --- | --- |
| `deploy.yml` | `dev` push → 자동 배포 |
| `db-migrate.yml` | 마이그레이션 한 장을 서버 DB 에 적용 (수동) |
| `place-search-ingest.yml` · `territory-sites-ingest.yml` | 데이터 적재 (수동) |
| `walk-diary-runtime.yml` | 산책 일기 런타임 점검 (수동) |
