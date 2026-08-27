# migrations/ — 개발용 시드만 남았다

스키마 변경은 **alembic 한 경로**다. 이 디렉터리에 `.sql` 을 넣어도 아무도 실행하지 않는다.

```bash
uv run alembic revision -m "무엇을 바꾸는지"   # alembic/versions/ 에 파일 생성
uv run alembic upgrade head
```

`dev_seed.sql` 은 마이그레이션이 아니라 개발용 시드다. 계속 수동으로 쓴다.

```bash
docker compose exec -T db psql -U daengs -d daengs < migrations/dev_seed.sql
```

## 옛 `001_init.sql` ~ `011_*.sql` 은 어디 갔나

지웠다. **중복이었기 때문이다.**

`alembic/versions/0001_001_init.py` ~ `0012_011_anchor.py` 가 각 파일의 내용을 **주석까지
한 글자도 안 바꾸고** 통째로 들고 있다. 리비전이 실행하는 것도 그 안의 `SQL` 문자열이지
디스크의 파일이 아니다 — `run_legacy_sql(name, sql)` 은 SQL 을 인자로 받고, `name` 은 실패
메시지와 `--sql` 출력의 라벨로만 쓴다 (`app/core/migration_sql.py`).

그래서 두 벌이 있었고, 한쪽만 고치면 조용히 어긋나는 자리였다. 원문은 git history 에 있다.

```bash
git log --oneline -- migrations/001_init.sql        # 언제 무엇이 바뀌었나
git show <커밋>:migrations/001_init.sql             # 그때 내용 그대로
```

`011` 이 두 개였던 것도 리비전 쪽에 그대로 남아 있다 (`0011_011_walk_fix_chain` ·
`0012_011_anchor`). 서로 다른 브랜치가 같은 번호를 집어 main 에서 만났고, 둘이 각각 다른
테이블을 건드려서 **우연히** 무사했다. 번호로 순서를 정하던 방식이 실패한 지점이고
alembic 을 넣은 이유다. 리비전 순서는 파일명이 아니라 main 에 도착한 순서를 따른다.

## initdb 로는 안 돈다

`docker-compose.yml` 은 이 디렉터리를 `/docker-entrypoint-initdb.d` 에 마운트하지 **않는다.**
그 방식은 볼륨 최초 생성 때만 돌아서 두 번째 변경부터 조용히 건너뛰기 때문이다. 그래서
alembic 도입 전부터 쓰던 DB 는 어디까지 적용됐는지 각자 다르다. 일괄 stamp 하지 말고
판별부터 한다:

```bash
uv run python -m scripts.detect_schema_revision
```
