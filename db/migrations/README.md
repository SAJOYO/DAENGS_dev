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
