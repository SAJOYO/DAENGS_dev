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

```powershell
docker compose exec -T pgvector psql -U <앱계정> -d vectordb -f - < db/migrations/<파일>.sql
```

파일 이름은 `YYYY-MM-DD_무엇을.sql` 입니다. 날짜순으로 적용하세요.
