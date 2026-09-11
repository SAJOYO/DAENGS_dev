# db/migrations

**이미 돌고 있는 DB** 에 손으로 적용하는 SQL 입니다.

`db/init/` 은 **볼륨이 비어 있을 때 한 번만** 실행됩니다. 서버 DB 는 이미 데이터가
들어 있어서, `db/init/*.sql` 을 고쳐도 서버에는 아무 일도 일어나지 않습니다.
그래서 스키마를 바꾼 PR 은 **두 곳을 같이** 고칩니다.

| | 대상 | 언제 |
| --- | --- | --- |
| `db/init/*.sql` | 새로 만드는 빈 DB | 볼륨이 빌 때 자동 |
| `db/migrations/*.sql` | 이미 있는 DB (서버) | 배포 후 사람이 직접 |

Alembic 을 쓰지 않는 것은 의도한 선택입니다 (CLAUDE.md). 버전 테이블도 없어서
**무엇이 적용됐는지 DB 가 기억하지 않습니다.** 각 파일이 여러 번 실행해도 안전하도록
(`IF NOT EXISTS`, `DROP ... IF EXISTS`) 쓰고, 적용 여부가 헷갈리면 다시 돌리세요.

## 적용

서버 PC 앞에서:

```powershell
docker compose exec -T pgvector psql -U <앱계정> -d vectordb -f - < db/migrations/<파일>.sql
```

파일 이름은 `YYYY-MM-DD_무엇을.sql` 입니다. 날짜순으로 적용하세요.

### 서버에 못 갈 때 — Actions 탭에서

`.github/workflows/db-migrate.yml` (**DB 마이그레이션 적용**)을 손으로 돌리면 됩니다.
서버 PC 에는 SSH 도 SMB 도 없지만 self-hosted 러너가 거기서 돌기 때문에, Actions 탭이
원격 손이 됩니다 (`place-search-ingest` 와 같은 방식).

입력은 셋입니다 — `file`(이 폴더 안의 파일 이름), `ref`(그 파일을 꺼내 올 브랜치.
**아직 안 머지된 PR 브랜치도 됩니다**), `verify`(적용 뒤 `verify_` 파일도 돌릴지).

⚠️ **그래서 순서를 뒤집을 수 있습니다.** 마이그레이션은 전부 덧붙이기만 하므로
(새 컬럼은 nullable, 새 표는 신규) **옛 코드가 도는 상태에서 먼저 적용해도 안전**하고,
그러는 편이 낫습니다 — 코드가 먼저 배포되면 그 사이에 없는 컬럼을 SELECT 해서
멀쩡하던 경로가 500 이 됩니다. 스키마를 바꾸는 PR 은 **머지 전에 이 워크플로를
그 브랜치로 한 번 돌리고** 머지하세요.

⚠️ 이 워크플로는 **작업 트리를 안 건드립니다.** `backend/src` 가 컨테이너에 bind mount
돼 있어서 브랜치를 checkout 하면 그 순간 backend 가 그 코드를 로드합니다. 그래서
`git show <ref>:<파일>` 로 SQL 만 꺼내 psql 에 흘려보냅니다.


## ⚠️ 표 일부는 `postgres` 소유다 — `must be owner of table` 의 정체 (2026-09-08, #329)

✅ **2026-09-10 에 갈렸다 — 이 절이 맞다.** 2026-09-09 에 사람이 *"daengs 도 슈퍼유저이고 여태
그걸로 했다"* 고 해서(#384) 한동안 🔴 로 매달려 있었는데, 그 말이 틀렸다.
앞의 두 줄은 `RAG-084` ⑩ 이 확인했고, **세 번째 줄은 그것이 열어 둔 물음의 답이다** —
⑩ 은 *"개발 PC `.env` 의 `POSTGRES_USER` 가 `postgres` 라 `db-migrate.yml` 은 그 계정으로 붙는다
(서버 `.env` 확인 필요)"* 로 끝났는데, #427 이 워크플로를 실제로 돌려서 **아니라는 것**을 봤다.

| 확인한 것 | 실측 (2026-09-10, 집 서버 `vectordb`) |
| --- | --- |
| `daengs` 가 슈퍼유저인가 | **아니다** — `usesuper = false`. `t` 인 것은 `postgres` 뿐이다 (`dog_rag` 도 f) |
| `documents`·`crawl_runs` 소유자 | 둘 다 **`postgres`** — 아래 「지금 상태」 표가 맞다 |
| **서버 `.env` 의 `POSTGRES_USER`** | **`daengs`** (#427) — 그래서 `db-migrate.yml` 이 `documents` 에 `ERROR: must be owner of table documents` 로 실패한다 (run `34457746365`) |

⚠️ **GCP 는 반대다.** 그 VM 의 `daengs` 는 **슈퍼유저**이고(`usesuper = t`, 2026-09-10 SSH 실측)
`docs/deploy/runbook.md` §3 도 *"이 VM 의 수퍼유저는 `postgres` 가 아니다"* 라고 적어 뒀다.
**같은 이름의 계정이 두 DB 에서 권한이 다르다** — 한쪽에서 됐다고 다른 쪽을 넘겨짚지 마라.

```sql
SELECT usesuper FROM pg_user WHERE usename = current_user;
SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname IN ('documents','crawl_runs');
```

`db-migrate.yml` 은 `-U $POSTGRES_USER` 로 붙는데 **서버 compose 의 그 값이 `daengs`** 이고
슈퍼유저가 아니다. 그런데 서버 DB 의 표 일부는 **`postgres`** 소유다 — `db/init/` 이 볼륨을
처음 만들 때 그 계정으로 돌았기 때문이다. 나머지는 나중에 `daengs` 가 만들어서 갈렸다.

⚠️ **개발 PC 루트 `.env` 의 `POSTGRES_USER` 는 `postgres` 라서 이 값과 다르다.** 그것을 보고
*"워크플로도 postgres 로 붙겠지"* 라고 읽으면 위 오류를 만난다 (2026-09-10 에 실제로 그렇게
읽었다). 워크플로가 보는 것은 **서버의** `.env` 이고, 개발 PC 의 것은 서버 컨테이너에 아무
영향이 없다.

`ALTER TABLE` 은 소유자만 할 수 있으므로 **그 표를 건드리는 마이그레이션은 워크플로로
적용되지 않는다.**

```
ERROR:  must be owner of table <표>
CONTEXT:  SQL statement "ALTER TABLE <표> DROP CONSTRAINT ..."
```

⚠️ **BEGIN/COMMIT 으로 감싼 마이그레이션은 여기서 통째로 롤백된다** — 반쯤 적용되지 않는다.
#329 에서 실제로 그랬고 DB 는 그대로였다. 감싸지 않은 파일은 그 보장이 없다.

### 지금 상태

| 소유자 | 표 |
| --- | --- |
| `daengs` | 대부분 (#329 에서 `training_rag_chunks` · `training_rag_documents` 를 옮겼다) |
| **`postgres`** | **`documents` · `crawl_runs`** — 아직 남아 있다 |

**이 둘을 건드릴 일이 생기면 먼저 옮겨야 한다.** 지금 옮겨 두지 않은 것은 각각 Life RAG ·
크롤러 쪽 표라 그 카드에서 판단할 몫이기 때문이다. **#427 도 옮기지 않고 지나갔다** — 아래
「소유자 계정으로 직접」이 있어서 그 카드에는 필요가 없었고, 소유권을 옮기는 것은 권한 모델을
바꾸는 일이라 그 자체로 사람이 정할 몫이다.

### 옮기지 않고 적용하는 법 — **개발 PC 에서 LAN 으로** (2026-09-10 · #427)

`postgres` 의 자격이 **개발 PC 루트 `.env` 의 `POSTGRES_USER`·`POSTGRES_PASSWORD` 에 있다.**
DB 포트가 일부러 LAN 에 열려 있으므로(CLAUDE.md) **서버 PC 앞에 가지 않고** 적용할 수 있다 —
`documents` 의 HNSW 인덱스(#427)를 그렇게 적용했다.

이 저장소에는 `psql` 이 없어도 된다. `backend` 의 psycopg 로 붙으면 되고, **파라미터를 안 넘기면
psycopg3 이 simple query protocol 을 쓰므로 파일 하나에 여러 문장이 있어도 그대로 보낸다** —
`verify_*.sql` 의 `DO` 블록 + `SELECT` 가 그 모양이다.

⚠️ `DO` 블록 뒤의 `SELECT` 를 읽으려면 **`cur.nextset()` 으로 결과를 넘겨야 한다.** 커서는
첫 결과(`DO`, 레코드 없음)를 보고 있어서 바로 `fetchall()` 하면
`the last operation didn't produce records (command status: DO)` 가 난다.

### 옮기는 법 — 서버 PC 에서

**컨테이너 안에서는 비밀번호 없이 붙는다** — 공식 이미지가 로컬 소켓을 `trust` 로 두기 때문이다.

```powershell
docker exec -i pgvector psql -U postgres -d vectordb -c "ALTER TABLE <표> OWNER TO daengs;"
```

확인:

```powershell
docker exec -i pgvector psql -U daengs -d vectordb -c "SELECT relname, pg_get_userbyid(relowner) FROM pg_class WHERE relname='<표>';"
```

### 서버 PC 터미널의 함정 셋

1. **`docker compose exec` 를 쓰지 마라.** 배포 폴더가 아닌 checkout 에서 치면
   `REDIS_PASSWORD` · `DAENGS_CORPUS_DIR` 이 없어 **SQL 을 실행하기도 전에** compose 가 설정
   해석 단계에서 죽는다. `docker exec` 는 `.env` 를 안 본다 (컨테이너 이름은 `pgvector`).
2. **PowerShell 은 `<` 입력 리다이렉션을 안 받는다** (`'<' 연산자는 나중에 사용하도록
   예약되어 있습니다`). 파일을 먹이려면 `cmd /c "... < 파일"` 로 감싼다.
3. **SQL 을 PowerShell 파이프에 태우지 마라** (`Get-Content ... | docker exec`). 파이프를
   지나며 다시 인코딩돼 한글 주석이 깨진다 — `db-migrate.yml` 이 `shell: cmd` 를 쓰는 이유와
   같다.

## 검증 성공의 의미

`verify=true`(기본값)이면 같은 ref의 `verify_<파일>`이 없거나 비어 있을 때
**DB 기동·백업·적용 전에 실패**한다. `verify=false`는 검증 SQL이 없는 기존 파일을
명시적으로 적용할 때만 사용한다.

⚠️ **그래서 verify 가 없는 마이그레이션은 Actions 탭으로 다시 못 돌린다** — 손으로
`verify=false` 를 골라야 한다. 2026-09-07(#292)까지 여덟 장이 그 상태였고 전부 아래
"단언형" 규칙이 생기기 전에 쓰인 것들이다.

**지금은 `db/migrations/*.sql` 전부가 짝을 가지고, 그 짝이 전부 단언형이며, 전부 하네스에
등록돼 있다** (#292 · #295). 그리고 그 셋을 CI 가 검사한다 — 아래 "단언형으로 쓴다" 참고. SQL은 cmd 리다이렉션으로 바이트를 보존하며,
Apply/Verify 모두 `psql -X -v ON_ERROR_STOP=1`의 오류를 작업 실패로 전달한다.

## `verify_*.sql` 은 **단언형으로 쓴다** (2026-09-06, #273)

검증 SQL은 테이블·컬럼 형식/NULL 허용·PK/FK/UNIQUE/CHECK와 관련 인덱스를 카탈로그에서
검사하고 **불일치 시 `RAISE EXCEPTION` 을 낸다.** 이는 스키마 검증이며 데이터나 실제
앱 연동을 검증한 것은 아니다. 파일 존재만으로 자동 스키마 판정을 보장하지 않는다.

⚠️ **출력 전용(SELECT 나열) verify 는 쓰지 않는다.** Apply/Verify 는
`psql -X -v ON_ERROR_STOP=1 … || exit 1` 로 **종료 코드**를 보는데, SELECT 만 있으면
스키마가 어떻든 0으로 끝난다 — 즉 **틀려도 녹색이다.** 2026-09-06 에 여섯 장이 그 상태였고
(`dog_cards` · `pet_photo` · `screening_records` · `answer_reports` · `app_user_nickname` ·
`documents_org_backfill`) 하필 그 아홉 장을 GCP 에 손으로 적용하기 전날 발견했다.

사람이 눈으로 볼 분포·샘플 질의는 **단언 뒤에** 둔다. 단언이 먼저 실패하면 거기서 멈춘다.

**그리고 `tools/check_migration_verification.py` 의 `CHECKS` 목록에 등록한다.** 그 하네스가
일회용 Postgres 에 마이그레이션을 적용한 뒤 스키마를 **일부러 망가뜨려 verify 가 잡는지** 본다 —
등록하지 않으면 단언형으로 써 놓고도 그 단언이 실제로 작동하는지는 아무도 안 잰다.

✅ **2026-09-07(#295)부터 이 두 가지를 기계가 본다** — `check_migration_verification.py coverage`
가 `db/migrations/*.sql` 전부에 대해 ⓐ 짝이 있고 ⓑ `RAISE EXCEPTION` 을 갖고 ⓒ `CHECKS` 에
등록됐는지 검사하고, CI 의 `names` job 이 그것을 돌린다. **같은 부채가 두 번 났기 때문이다** —
#273 이 여섯 장을 고쳤는데 여덟 장이 그때 목록에 안 들어갔고 #292 가 그것을 다시 발견했다.
목록을 사람이 관리하는 한 세 번째가 온다.

예외는 코드 안의 `BEHAVIOURAL` 하나뿐이고 지금 한 줄이다 — `2026-08-31_pets` 는 카탈로그
단언이 아니라 **행동 테스트**라(자기 스키마를 만들고 `\i db/init/*.sql` 을 부르고 INSERT 로
FK 동작을 본다) 하네스의 격리 모델과 안 맞는다. **못 넣는 것이지 부실한 것이 아니다.**
여기 이름을 더할 때는 *왜 하네스 모델과 안 맞는지*를 적는다 — "나중에 하자"는 이유가 아니다.

⚠️ **예외 문구는 공용 어휘를 쓴다** — `missing table:` · `... mismatch:`. 하네스가
"verifier 가 잡았다"와 "적용이 실패했다"를 그 낱말로 가르기 때문에, 다른 말로 쓰면
**변조를 잡았는데도 하네스가 실패로 읽는다.**
등록에 필요한 것은 (날짜, 이름, 픽스처, 테이블, 변조 목록) 다섯이고, 변조는
**그 마이그레이션이 세운 것을 하나씩 무너뜨리는** 방식으로 고른다.

두 가지 함정이 있다:

- **정의 문자열을 그대로 비교하지 않는다.** Postgres 가 다시 써서 내놓는다 —
  `lower(nickname)` 은 `lower((nickname)::text)` 로 보인다. 표현식 인덱스인지는
  `pg_index.indexprs` 로 보고, 문자열은 함수 이름 정도만 본다.
- **값을 바꾸는 마이그레이션은 값도 망가뜨려야 한다.** 모양만 검사하면
  `documents_org_backfill` 처럼 "컬럼은 멀쩡한데 값이 다 지워진" 상태를 놓친다.
  실제로 그 사고가 났다 (`docs/life/decisions-rag.md` RAG-066 ①).

### `verify_<옛것>` 은 `<새것>` 까지 적용된 DB 위에서도 돈다 (2026-09-07, #292)

버전 테이블이 없어서 **아무 때나 아무 verify 나 다시 돌릴 수 있는 것**이 이 폴더의 성질인데,
옛 장이 만든 것을 뒤 장이 걷어 가거나 넓히는 경우가 실제로 넷 있다:

| 옛 장 | 뒤 장이 한 것 | verify 가 대응하는 법 |
| --- | --- | --- |
| `2026-08-31_walks` 의 `walks.pet_id` | `2026-09-01_walk_pets` 가 다대다로 옮기고 `DROP COLUMN` | **있을 때만** 검사 |
| `2026-08-31_walks` 의 `walk_points` | `2026-09-02_walk_point_chunks` 가 `DROP TABLE` | **있을 때만** 검사 |
| `2026-08-29_crawl_runs` 의 `trigger` CHECK | `2026-08-30_..._trigger_revision` 이 값을 하나 더함 | 개수를 세지 않고 **들어 있는지만** |
| `2026-08-28_documents_lexical` 의 기본값 | 같은 파일의 **2단계**(주석)가 적재 뒤에 뗀다 | **토큰이 찼을 때만** 단언 |

**조건 없이 단언하면 그 verify 가 운영 DB 전부에서 실패한다.** 반대로 조건을 너무 넓게 잡으면
아무것도 안 잡는다. 판정 기준은 마이그레이션 본문이 스스로 적어 둔 조건을 그대로 쓴다 —
예를 들어 `documents_lexical` 의 2단계 조건은 `content_tokens = ''` 가 0행인 것이고,
verify 도 같은 것을 본다.

산책 두 파일은 `2026-09-05_walk_entries.sql`, `2026-09-05_walk_storyboards.sql`로
이름을 통일했다. SQL 내용은 그대로이며 DB 적용 이력 테이블을 도입하지 않는다.
이미 적용된 DB를 파일명 변경 때문에 되돌릴 필요는 없고, 적용 여부는 검증 SQL로 확인한다.
같은 날짜 안에서도 FK 등 실제 의존성을 확인하고, 스키마 적용·검증 후 코드를 배포한다.
