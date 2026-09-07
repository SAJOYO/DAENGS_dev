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


## 검증 성공의 의미

`verify=true`(기본값)이면 같은 ref의 `verify_<파일>`이 없거나 비어 있을 때
**DB 기동·백업·적용 전에 실패**한다. `verify=false`는 검증 SQL이 없는 기존 파일을
명시적으로 적용할 때만 사용한다. SQL은 cmd 리다이렉션으로 바이트를 보존하며,
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

**그리고 `tools/check_migration_verification.py` 의 목록에 등록한다.** 그 하네스가 일회용
Postgres 에 마이그레이션을 적용한 뒤 스키마를 **일부러 망가뜨려 verify 가 잡는지** 본다 —
등록하지 않으면 단언형으로 써 놓고도 그 단언이 실제로 작동하는지는 아무도 안 잰다.
등록에 필요한 것은 (날짜, 이름, 픽스처, 테이블, 변조 목록) 다섯이고, 변조는
**그 마이그레이션이 세운 것을 하나씩 무너뜨리는** 방식으로 고른다.

두 가지 함정이 있다:

- **정의 문자열을 그대로 비교하지 않는다.** Postgres 가 다시 써서 내놓는다 —
  `lower(nickname)` 은 `lower((nickname)::text)` 로 보인다. 표현식 인덱스인지는
  `pg_index.indexprs` 로 보고, 문자열은 함수 이름 정도만 본다.
- **값을 바꾸는 마이그레이션은 값도 망가뜨려야 한다.** 모양만 검사하면
  `documents_org_backfill` 처럼 "컬럼은 멀쩡한데 값이 다 지워진" 상태를 놓친다.
  실제로 그 사고가 났다 (`docs/life/decisions-rag.md` RAG-066 ①).

산책 두 파일은 `2026-09-05_walk_entries.sql`, `2026-09-05_walk_storyboards.sql`로
이름을 통일했다. SQL 내용은 그대로이며 DB 적용 이력 테이블을 도입하지 않는다.
이미 적용된 DB를 파일명 변경 때문에 되돌릴 필요는 없고, 적용 여부는 검증 SQL로 확인한다.
같은 날짜 안에서도 FK 등 실제 의존성을 확인하고, 스키마 적용·검증 후 코드를 배포한다.
