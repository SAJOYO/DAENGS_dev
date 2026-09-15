# AI 카드 한도 제품 규칙 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 테스트 단계용 AI 카드 한도(`check_quota`)를 사용자가 2026-09-15 에 정한 제품 규칙으로 통째로 바꾼다 — 지워도 안 돌아오는 하루 1회, 강아지마다 달마다 한 장, 제목에만 쓰는 이름, 목록의 남은 횟수.

**Architecture:** 카드가 `ready` 가 되는 순간 `ai_card_usage` 에 한 줄을 남기고(카드를 지워도 남음) 하루 한도는 그 줄 수로 센다. 달별 한 장은 `ai_cards` 의 살아 있는 `ready`/`generating` 행으로 거른다(돈이 나가기 전, `start` 의 동기 구간). 제목 이름·남은 횟수는 라우터 쿼리/응답 칸 하나씩이다.

**Tech Stack:** FastAPI · SQLAlchemy 2.0 async · PostgreSQL(스키마 원본은 `db/init/*.sql`, Alembic 없음) · pytest(가짜 리포지토리 `tests/fakes.py`) · uv(Python 3.12)

**Spec:** PR #543 본문(`gh pr view 543`) 의 「무엇을 / 왜」 표와 「작업 목록」. 기존 설계는 `docs/cardimage/spec-2026-09-14-app-ai-cards.md` §3·§5·§6.

## Global Constraints

- 파일 읽기·쓰기·수정은 **Read / Edit / Write 도구로만**. heredoc·`sed`·`echo >` 로 파일을 만들지 않는다 (Windows + Git Bash 에서 한글·백슬래시가 깨진다).
- 모든 Python 실행은 `backend/` 에서 `uv run ...` 으로. `uv sync` 를 인자 없이 돌리지 않는다. 의존성 추가 없음.
- **`git add -A` / `git add .` 금지.** 이 태스크가 만든·고친 파일만 경로로 `git add` 한다 (공유 워킹트리에 남의 산출물이 있을 수 있다).
- 커밋 메시지는 한국어 `타입: 무엇을 (#543)`, 끝에 빈 줄 + `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **전체 `uv run pytest` 는 태스크 구현자가 돌리지 않는다** (9분, 다른 에이전트와 겹침). 각 태스크는 자기 테스트 파일만 돌린다. 전체는 Task 5 에서 컨트롤러가 돈다.
- 기본 DB 스키마 원본은 `db/init/*.sql`, `models/` 는 따라가는 쪽. 이미 있는 DB 용 SQL 은 `db/migrations/YYYY-MM-DD_이름.sql` + `verify_YYYY-MM-DD_이름.sql`(단언형, `RAISE EXCEPTION`) 짝이고 **여러 번 돌려도 안전**해야 한다.
- 시간 기준은 KST(`services/ai_card_quota.py::KST`, `kst_day_start`).
- 앱이 그대로 띄우는 `message` 는 한국어 문장이고 환경 변수 이름·예외 문구를 담지 않는다.
- 규칙(사용자 결정, 2026-09-15 — 바꾸지 말 것):
  - 하루 한도 **1회**(KST, `DAENGS_CARDIMAGE_DAILY_LIMIT` 기본 1, 0 이면 무제한). 누끼 카드와 따로 센다.
  - **지워도 횟수는 돌아오지 않는다.** 서버가 그리다 **실패한 카드는 세지 않는다.**
  - **강아지마다 달마다 한 장**: 같은 `app_user_id`·`dog_id`·`month` 에 `ready`/`generating` 행이 있으면 `409 month_taken`. `dog_id` 가 없으면 검사 안 함. 보호자마다 따로 센다.
  - `title_name` 선택 입력 — 있으면 제목만 `<카드명> <title_name>`, 비우면 `dog_name`. `dog_name` 은 그대로 저장.
  - `GET /app/ai-cards` 에 `daily_limit`·`daily_remaining` (무제한이면 둘 다 `null`).
  - 동시 1장(`409 already_generating`)·돈 나간 실패 하루 5번(`PAID_FAILURE_CODES`)은 **그대로**.

## 파일 지도

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `db/init/39_ai_card_usage.sql` (새) | 사용 기록 표 원본 | 1 |
| `db/migrations/2026-09-15_ai_card_usage.sql` (새) | 운영 DB 적용 + 기존 `ready` 카드 백필 | 1 |
| `db/migrations/verify_2026-09-15_ai_card_usage.sql` (새) | 단언형 검증 | 1 |
| `tools/check_migration_verification.py` | `CHECKS` 에 변조 목록 등록 | 1 |
| `backend/src/daengs_backend/models/ai_card.py` · `models/__init__.py` | `AiCardUsage` 모델 | 1 |
| `backend/tests/test_ai_card_model.py` | 모델이 SQL 을 따라가는지 | 1 |
| `backend/src/daengs_backend/repositories/ai_card.py` | 사용 기록 쿼리·달별 조회, `count_ready_since` 삭제 | 2 |
| `backend/tests/fakes.py` | 위 리포지토리의 가짜 | 2 |
| `backend/src/daengs_backend/services/ai_card_quota.py` | `check_quota` 교체, `AiCardMonthTakenError`, `daily_remaining` | 2 |
| `backend/tests/test_ai_card_quota.py` | 한도 규칙 | 2 |
| `backend/.env.example` | 한도 주석 한 줄 | 2 |
| `backend/src/daengs_backend/services/ai_card.py` | ready 때 기록, `title_name`, `daily_status`, 탈퇴 정리 | 3 |
| `backend/tests/test_ai_card_service.py` | 서비스 규칙 | 3 |
| `backend/src/daengs_backend/routers/ai_card.py` · `schemas/ai_card.py` | 쿼리 `title_name`, 409 `month_taken`, 목록 칸 둘 | 4 |
| `backend/tests/test_ai_cards_api.py` | HTTP 경계 | 4 |
| `docs/decisions.md` · `docs/cardimage/README.md` · `spec-2026-09-14-app-ai-cards.md` · `worklog.md` | D-077 · 정해진 것 · §5 · 작업 기록 | 5 |

---

### Task 1: DB — 사용 기록 표 `ai_card_usage` + 모델

**Files:**
- Create: `db/init/39_ai_card_usage.sql`
- Create: `db/migrations/2026-09-15_ai_card_usage.sql`
- Create: `db/migrations/verify_2026-09-15_ai_card_usage.sql`
- Modify: `tools/check_migration_verification.py` (`CHECKS = (` 바로 아래, 맨 앞 항목으로)
- Modify: `backend/src/daengs_backend/models/ai_card.py` (파일 끝에 클래스 추가)
- Modify: `backend/src/daengs_backend/models/__init__.py:44`(import) · `__all__`
- Test: `backend/tests/test_ai_card_model.py`

**Interfaces:**
- Produces: `daengs_backend.models.AiCardUsage` — 칸 `card_id: uuid.UUID`(PK, **FK 없음**) · `app_user_id: uuid.UUID`(FK `app_users.id` CASCADE) · `used_at: datetime`(timestamptz). 표 이름 `ai_card_usage`, 인덱스 `idx_ai_card_usage_owner_used`.

설계 요점 (구현자가 알아야 할 이유):
- `card_id` 에 **FK 를 걸지 않는다.** 카드를 지우면 행이 사라지는데, FK 가 있으면 CASCADE 든 RESTRICT 든 "지워도 안 돌아온다"가 깨지거나 삭제가 막힌다.
- `card_id` 가 PK 라 카드 하나에 기록은 하나 — 백필을 두 번 돌려도 `ON CONFLICT DO NOTHING` 으로 같은 결과.
- 탈퇴는 `app_users` 행을 남기므로 CASCADE 는 안 돈다(`ai_cards` 와 같다). 정리는 Task 3 이 명시로 한다.

- [ ] **Step 1: 모델 테스트를 먼저 쓴다** — `backend/tests/test_ai_card_model.py` 의 import 줄을 바꾸고 파일 끝에 추가

import 줄 교체:

```python
from daengs_backend.models import AI_CARD_STATUSES, AiCard, AiCardUsage
```

파일 끝에 추가:

```python
def test_usage_columns_follow_sql() -> None:
    """`db/init/39_ai_card_usage.sql` 을 따라간다 (#543, D-077)."""
    table = AiCardUsage.__table__
    assert table.name == "ai_card_usage"
    assert set(table.c.keys()) == {"card_id", "app_user_id", "used_at"}
    assert [c.name for c in table.primary_key.columns] == ["card_id"]


def test_usage_card_id_has_no_foreign_key() -> None:
    """카드를 지워도 사용 기록은 남아야 한다 — FK 가 생기면 삭제가 기록을 끌고 가거나 막힌다."""
    assert not AiCardUsage.__table__.c.card_id.foreign_keys
    fks = list(AiCardUsage.__table__.c.app_user_id.foreign_keys)
    assert len(fks) == 1 and fks[0].target_fullname == "app_users.id" and fks[0].ondelete == "CASCADE"
    assert "idx_ai_card_usage_owner_used" in {i.name for i in AiCardUsage.__table__.indexes}
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_ai_card_model.py -q`
Expected: FAIL — `ImportError: cannot import name 'AiCardUsage'`

- [ ] **Step 3: 모델** — `backend/src/daengs_backend/models/ai_card.py` 끝에 추가 (import 는 이미 다 있다)

```python
class AiCardUsage(Base):
    """AI 카드 하루 한도를 세는 사용 기록 (#543, D-077). 스키마 원본은 `db/init/39_ai_card_usage.sql`.

    카드가 `ready` 가 되는 순간 한 줄. **카드를 지워도 남습니다** — 그래서 `card_id` 에 FK 가 없습니다.
    """

    __tablename__ = "ai_card_usage"

    __table_args__ = (Index("idx_ai_card_usage_owner_used", "app_user_id", "used_at"),)

    card_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    #: ⚠️ **이 CASCADE 에 기대면 안 됩니다.** 탈퇴는 `app_users` 행을 남깁니다 — `cleanup_for_owner` 가 지웁니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id", ondelete="CASCADE"))

    used_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    def __repr__(self) -> str:
        return f"<AiCardUsage {self.card_id} {self.used_at}>"
```

`models/__init__.py` 44행을 `from daengs_backend.models.ai_card import AI_CARD_STATUSES, AiCard, AiCardUsage` 로 바꾸고, `__all__` 의 `"AiCard",` 바로 다음 줄에 `"AiCardUsage",` 를 넣는다.

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_ai_card_model.py -q`
Expected: 5 passed

- [ ] **Step 5: `db/init/39_ai_card_usage.sql`**

```sql
-- ---------------------------------------------------------------------
-- ai_card_usage : AI 도감 카드 하루 한도를 세는 사용 기록 (#543, D-077, docs/cardimage/)
-- ---------------------------------------------------------------------
-- 카드가 'ready' 가 되는 순간 backend 가 한 줄 남긴다. 하루 한도는 이 표의 KST 오늘 줄 수다.
--
-- ⚠️ **card_id 에 FK 를 걸지 않는다.** 카드를 지워도 이 줄은 남아야 한다 — "지워도 횟수는
--    돌아오지 않는다"(사용자 결정 2026-09-15). FK 를 걸면 CASCADE 는 횟수를 돌려주고,
--    RESTRICT 는 카드 삭제를 막는다.
-- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴는 app_users 행을 남기므로 이 CASCADE 는 영영 안 돈다 —
--    탈퇴 경로(services/ai_card.py::cleanup_for_owner)가 명시로 지운다.
-- 실패한 카드는 줄을 남기지 않는다 — 실패는 한도에 세지 않는다.

CREATE TABLE IF NOT EXISTS ai_card_usage (
    -- 카드 하나에 기록 하나. 백필을 여러 번 돌려도 ON CONFLICT 로 같은 결과가 된다.
    card_id UUID PRIMARY KEY,
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 한도는 늘 "내 오늘 기록 수".
CREATE INDEX IF NOT EXISTS idx_ai_card_usage_owner_used
    ON ai_card_usage (app_user_id, used_at DESC);
```

- [ ] **Step 6: `db/migrations/2026-09-15_ai_card_usage.sql`** — init 과 같은 내용 + 백필

```sql
-- 이미 돌고 있는 DB 에 적용 (db/init/39_ai_card_usage.sql 과 같은 표 + 기존 카드 백필, 여러 번 돌려도 안전).
-- ai_card_usage : AI 도감 카드 하루 한도를 세는 사용 기록 (#543, D-077, docs/cardimage/)
-- ---------------------------------------------------------------------
-- 카드가 'ready' 가 되는 순간 backend 가 한 줄 남긴다. 하루 한도는 이 표의 KST 오늘 줄 수다.
--
-- ⚠️ **card_id 에 FK 를 걸지 않는다.** 카드를 지워도 이 줄은 남아야 한다.
-- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴 경로가 명시로 지운다.
--
-- 배포 순서: **이 파일을 코드보다 먼저** 적용한다 (DB 먼저, 코드 나중).

CREATE TABLE IF NOT EXISTS ai_card_usage (
    card_id UUID PRIMARY KEY,
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_card_usage_owner_used
    ON ai_card_usage (app_user_id, used_at DESC);

-- 백필: 지금 남아 있는 'ready' 카드마다 한 줄. 안 옮기면 배포 당일 이미 만든 사람이 한 장 더 만든다.
-- used_at 은 ready 가 된 시각에 가장 가까운 updated_at. 이미 지운 카드는 기록할 방법이 없다.
INSERT INTO ai_card_usage (card_id, app_user_id, used_at)
SELECT id, app_user_id, updated_at FROM ai_cards WHERE status = 'ready'
ON CONFLICT (card_id) DO NOTHING;
```

- [ ] **Step 7: `db/migrations/verify_2026-09-15_ai_card_usage.sql`** — 단언형. 형식은 `verify_2026-09-14_ai_cards.sql` 과 같다.

```sql
-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — SELECT 나열이면 틀려도 종료 코드 0 이라 녹색이 된다 (#273 · #292).
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('ai_card_usage') IS NULL THEN
        RAISE EXCEPTION 'missing table: ai_card_usage';
    END IF;
    relation := to_regclass('ai_card_usage');

    FOR item IN SELECT * FROM (VALUES
        ('card_id', 'uuid', 'true'),
        ('app_user_id', 'uuid', 'true'),
        ('used_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: ai_card_usage.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- PK 는 card_id, FK 는 app_user_id → app_users CASCADE 하나뿐.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'card_id', NULL, NULL, NULL),
        ('f', 'app_user_id', 'app_users', 'id', 'c')
    ) AS expected(kind, columns, target_table, target_columns, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND (item.kind <> 'f' OR (
                  c.confrelid = to_regclass(item.target_table)
                  AND c.confdeltype::text = item.delete_action
                  AND ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num, pos)
                            JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num
                            ORDER BY k.pos) = string_to_array(item.target_columns, ',')
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: ai_card_usage kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- **card_id 에 FK 가 생기면 안 된다** — 카드를 지울 때 기록이 같이 사라져 횟수가 돌아온다.
    IF (SELECT count(*) FROM pg_constraint c WHERE c.conrelid = relation AND c.contype = 'f') <> 1 THEN
        RAISE EXCEPTION 'constraint mismatch: ai_card_usage must have exactly one FK (app_user_id)';
    END IF;

    definition := NULL;
    SELECT pg_get_indexdef(i.indexrelid) INTO definition
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    WHERE i.indrelid = relation AND c.relname = 'idx_ai_card_usage_owner_used'
      AND i.indisvalid AND i.indisready AND NOT i.indisunique;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'index mismatch: idx_ai_card_usage_owner_used missing, invalid, or unique';
    END IF;

    -- 백필: 남아 있는 ready 카드에는 전부 기록이 있어야 한다.
    IF EXISTS (
        SELECT 1 FROM ai_cards c
        WHERE c.status = 'ready'
          AND NOT EXISTS (SELECT 1 FROM ai_card_usage u WHERE u.card_id = c.id)
    ) THEN
        RAISE EXCEPTION 'backfill mismatch: ready ai_cards without ai_card_usage rows';
    END IF;
END
$verify$;

-- 사람이 눈으로 보는 자리. 적용 직후에는 ready 카드 수와 같다.
SELECT count(*) AS usage_rows FROM ai_card_usage;
```

- [ ] **Step 8: 하네스에 등록** — `tools/check_migration_verification.py` 의 `CHECKS = (` 다음 줄(현재 `('2026-09-14', 'ai_cards', ...` 바로 앞)에 넣는다. 먼저 `CHECKS` 를 소비하는 실행 함수를 읽어 **픽스처 → 마이그레이션(2회) → verify 통과 → 변조마다 verify 실패** 순서임을 확인한다.

```python
        # 사용 기록(#543). 픽스처에 ready 카드 한 장을 넣어 **백필이 실제로 돈다** — 'DELETE' 변조가 그것을 잰다.
        ('2026-09-15', 'ai_card_usage',
         APP_USERS + PETS_ONLY + SET_UPDATED_AT + prerequisites('2026-09-14_ai_cards')
         + "INSERT INTO ai_cards(id, app_user_id, month, dog_name, title, status,"
           " storage_key, generation, size_bytes, width, height) VALUES"
           " ('77777777-7777-7777-7777-777777777777', '11111111-1111-1111-1111-111111111111',"
           "  4, 'x', 'BLOSSOM X', 'ready', 'k', 'g', 1, 994, 1582);",
         'ai_card_usage', [
            'ALTER TABLE ai_card_usage DROP COLUMN used_at',
            'ALTER TABLE ai_card_usage ALTER COLUMN app_user_id DROP NOT NULL',
            'ALTER TABLE ai_card_usage DROP CONSTRAINT ai_card_usage_pkey',
            # **card_id 에 FK 가 붙는 변조** — 카드를 지우면 기록이 같이 사라져 횟수가 돌아온다.
            'ALTER TABLE ai_card_usage ADD FOREIGN KEY(card_id) REFERENCES ai_cards(id) ON DELETE CASCADE',
            'ALTER TABLE ai_card_usage DROP CONSTRAINT ai_card_usage_app_user_id_fkey;'
            ' ALTER TABLE ai_card_usage ADD FOREIGN KEY(app_user_id) REFERENCES app_users(id)',
            'DROP INDEX idx_ai_card_usage_owner_used',
            # 백필이 빠진 상태
            'DELETE FROM ai_card_usage',
        ]),
```

⚠ 괄호 모양은 바로 아래 기존 항목과 똑같이 맞춘다(들여쓰기 8칸 튜플). `prerequisites` 가 모듈에서 `CHECKS` 보다 먼저 정의돼 있는지 확인한다 (162행에 있다).

- [ ] **Step 9: 저장소 규칙 검사**

Run: `cd backend && uv run check`
Expected: 모든 검사 통과 (특히 `db/migrations 이름·짝` · `db/migrations 짝·단언·등록`)

- [ ] **Step 10: 변조 하네스를 실제로 돌린다** (리뷰어 손 추적으로 대신하지 않는다 — 09-15 #537 에서 손 추적이 틀렸다)

```bash
docker run -d --rm --name daengs-ci-pgvector-543 -p 55432:5432 \
  -e POSTGRES_PASSWORD=test-password -e POSTGRES_DB=migration_test \
  pgvector/pgvector:pg17
# 기동 대기 후 (psql 이 PATH 에 없으면 C:/Users/403/tools/pgsql/pgsql/bin 을 PATH 앞에 둔다)
PGHOST=127.0.0.1 PGPORT=55432 PGUSER=postgres PGPASSWORD=test-password \
PGDATABASE=migration_test uv run --no-project python tools/check_migration_verification.py sql
docker rm -f daengs-ci-pgvector-543
```

(저장소 루트에서 실행.) Expected: 실패 0, 출력에 `ai_card_usage` 항목의 변조 7개가 전부 잡힘. 하나라도 통과하면 verify SQL 을 고친다. **Docker 가 없거나 컨테이너가 안 뜨면 멈추고 컨트롤러에게 알린다** — 건너뛰고 커밋하지 않는다.

- [ ] **Step 11: 커밋**

```bash
git add db/init/39_ai_card_usage.sql db/migrations/2026-09-15_ai_card_usage.sql \
  db/migrations/verify_2026-09-15_ai_card_usage.sql tools/check_migration_verification.py \
  backend/src/daengs_backend/models/ai_card.py backend/src/daengs_backend/models/__init__.py \
  backend/tests/test_ai_card_model.py
git commit -m "feat: AI 카드 사용 기록 표 ai_card_usage — 지워도 남고 기존 ready 카드는 백필 (#543)"
```

---

### Task 2: 리포지토리 + 가짜 + 한도 규칙 교체

**Files:**
- Modify: `backend/src/daengs_backend/repositories/ai_card.py` (`count_ready_since` 삭제, 함수 넷 추가)
- Modify: `backend/tests/fakes.py:258-259`(Store 칸) · `:1885-1890`(가짜 교체) · `:1933`(monkeypatch)
- Modify: `backend/src/daengs_backend/services/ai_card_quota.py` (전체)
- Modify: `backend/.env.example:229`
- Test: `backend/tests/test_ai_card_quota.py` (전체 다시 씀)

**Interfaces:**
- Consumes: `daengs_backend.models.AiCardUsage` (Task 1)
- Produces:
  - `ai_card_repo.add_usage(session: AsyncSession, usage: AiCardUsage) -> AiCardUsage` (동기, `session.add`)
  - `async ai_card_repo.count_usage_since(session, app_user_id: uuid.UUID, since: datetime) -> int`
  - `async ai_card_repo.delete_usage_for_owner(session, app_user_id: uuid.UUID) -> int`
  - `async ai_card_repo.has_month_card(session, app_user_id: uuid.UUID, dog_id: uuid.UUID, month: int) -> bool`
  - `ai_card_quota.AiCardMonthTakenError(Exception)`
  - `async ai_card_quota.check_quota(session, app_user_id, *, now: datetime, daily_limit: int, dog_id: uuid.UUID | None, month: int) -> None` — **`dog_id`·`month` 는 키워드 필수**(빠뜨려서 달별 검사가 조용히 꺼지는 일을 막는다)
  - `async ai_card_quota.daily_remaining(session, app_user_id, *, now: datetime, daily_limit: int) -> int | None` — 무제한(0)이면 `None`
  - 가짜 `Store.ai_card_usage: list`

⚠ 한도를 사용 기록으로 세는 순간 **기록을 남기는 쪽이 없으면** 기존 `test_ready_card_uses_up_daily_limit`(서비스)·`test_daily_limit_is_429`(API) 가 깨지고, `check_quota` 시그니처가 바뀌어 `services/ai_card.py:124` 도 깨진다. 그래서 Step 7 에서 서비스의 **두 자리만** 최소로 고친다 — `check_quota` 호출과 `_finish_ready` 의 기록 한 줄. 나머지 서비스 변경(`title_name`·`daily_status`·탈퇴 정리)은 Task 3.

- [ ] **Step 1: 한도 테스트를 새 규칙으로 다시 쓴다** — `backend/tests/test_ai_card_quota.py` 전체를 아래로 교체

```python
"""`services/ai_card_quota.py` — 앱 사용자 AI 카드 생성 한도 (#537 · #543, D-076 · D-077).

제품 규칙(사용자 결정 2026-09-15): 동시 1장 · KST 하루 N회(**사용 기록**으로 셈 — 지워도 안 돌아옴,
실패는 안 셈) · 강아지마다 달마다 한 장(보호자마다 따로) · 돈 나간 실패 하루 5번.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAdmin, FakeAppUser, Store, install

from daengs_backend.config import Settings, settings
from daengs_backend.models import AiCard, AiCardUsage
from daengs_backend.services import ai_card_quota as quota

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
DOG = uuid.uuid4()
OTHER_DOG = uuid.uuid4()
NOW = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)  # KST 12:00
KST_TODAY_START = datetime(2026, 9, 13, 15, 0, tzinfo=UTC)  # KST 14일 00:00
KST_YESTERDAY_LAST = datetime(2026, 9, 13, 14, 59, tzinfo=UTC)  # KST 13일 23:59


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    monkeypatch.setattr(settings, "cardimage_timeout_ms", 120_000)
    return s


def _card(
    status: str,
    created_at: datetime,
    owner: uuid.UUID = OWNER,
    *,
    updated_at: datetime | None = None,
    error_code: str = "upstream",
    dog_id: uuid.UUID | None = None,
    month: int = 4,
) -> AiCard:
    return AiCard(
        id=uuid.uuid4(), app_user_id=owner, dog_id=dog_id, month=month, dog_name="네오", title="BLOSSOM 네오",
        status=status, error_code=error_code if status == "failed" else None,
        created_at=created_at, updated_at=updated_at if updated_at is not None else created_at,
    )


def _usage(used_at: datetime, owner: uuid.UUID = OWNER) -> AiCardUsage:
    return AiCardUsage(card_id=uuid.uuid4(), app_user_id=owner, used_at=used_at)


def _check(limit: int = 1, *, dog_id: uuid.UUID | None = None, month: int = 4) -> None:
    asyncio.run(quota.check_quota(None, OWNER, now=NOW, daily_limit=limit, dog_id=dog_id, month=month))


def _remaining(limit: int = 1) -> int | None:
    return asyncio.run(quota.daily_remaining(None, OWNER, now=NOW, daily_limit=limit))


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("DAENGS_CARDIMAGE_DAILY_LIMIT", "DAENGS_CARDIMAGE_CONCURRENCY"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.cardimage_daily_limit == 1
    assert s.cardimage_concurrency == 2


def test_stale_after_covers_worst_case_retry(store: Store) -> None:
    # 엔진·검수 120초씩 × 재시도 2회 = 8분. 그보다 1분 길다.
    assert quota.stale_after() == timedelta(minutes=9)


def test_kst_day_start() -> None:
    assert quota.kst_day_start(NOW) == KST_TODAY_START


def test_empty_is_allowed(store: Store) -> None:
    _check()


# ── 동시 1장 ────────────────────────────────────────────────────────────


def test_fresh_generating_is_busy(store: Store) -> None:
    store.ai_cards.append(_card("generating", NOW - timedelta(minutes=1)))
    with pytest.raises(quota.AiCardBusyError):
        _check()


def test_stale_generating_is_expired_not_busy(store: Store) -> None:
    # 정리 기준은 `updated_at` 이다 — 슬롯을 잡을 때마다 그 칸을 찍는다(_claim_slot).
    old = NOW - timedelta(minutes=10)
    card = _card("generating", old, updated_at=old)
    store.ai_cards.append(card)
    _check()
    assert card.status == "failed" and card.error_code == "interrupted"


def test_old_created_at_but_fresh_updated_at_is_busy_not_expired(store: Store) -> None:
    """오래 전에 만들어졌어도 슬롯을 최근에 잡았으면(_claim_slot 이 `updated_at` 을 찍음)
    아직 도는 작업이다 — 정리 기준이 `created_at` 이 아니라 `updated_at` 인 것을 지킨다."""
    card = _card("generating", NOW - timedelta(hours=2), updated_at=NOW - timedelta(minutes=1))
    store.ai_cards.append(card)
    with pytest.raises(quota.AiCardBusyError):
        _check()


# ── 하루 한도 — 사용 기록으로 센다 ──────────────────────────────────────


def test_usage_today_hits_limit(store: Store) -> None:
    store.ai_card_usage.append(_usage(KST_TODAY_START))
    with pytest.raises(quota.AiCardLimitError):
        _check()


def test_usage_yesterday_kst_does_not_count(store: Store) -> None:
    store.ai_card_usage.append(_usage(KST_YESTERDAY_LAST))
    _check()


def test_ready_row_without_usage_does_not_count(store: Store) -> None:
    """세는 곳이 `ai_cards` 가 아니라 사용 기록이다 — 기록이 없는 ready 행은 한도에 안 걸린다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(hours=1)))
    _check()


def test_failed_does_not_count(store: Store) -> None:
    store.ai_cards.append(_card("failed", NOW - timedelta(hours=1)))
    _check()


def test_zero_limit_is_unlimited(store: Store) -> None:
    for _ in range(5):
        store.ai_card_usage.append(_usage(NOW - timedelta(hours=1)))
    _check(limit=0)


def test_other_users_rows_are_ignored(store: Store) -> None:
    store.ai_cards.append(_card("generating", NOW, owner=STRANGER))
    store.ai_card_usage.append(_usage(NOW, owner=STRANGER))
    _check()


# ── 돈 나간 실패 — 그대로 ───────────────────────────────────────────────


def _failures(store: Store, n: int, code: str = "upstream", at: datetime = NOW - timedelta(hours=1)) -> None:
    for _ in range(n):
        store.ai_cards.append(_card("failed", at, error_code=code))


def test_five_paid_failures_today_hit_limit(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY)
    with pytest.raises(quota.AiCardLimitError):
        _check()


def test_four_paid_failures_today_are_ok(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY - 1)
    _check()


def test_interrupted_failures_do_not_count(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY, code="interrupted")
    _check()


def test_paid_failures_yesterday_kst_do_not_count(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY, at=KST_YESTERDAY_LAST)
    _check()


def test_paid_failure_cap_applies_even_when_unlimited(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY, code="no_image")
    with pytest.raises(quota.AiCardLimitError):
        _check(limit=0)


# ── 강아지마다 달마다 한 장 ─────────────────────────────────────────────


def test_same_dog_same_month_ready_is_taken(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, month=4))
    with pytest.raises(quota.AiCardMonthTakenError):
        _check(limit=0, dog_id=DOG, month=4)


def test_month_taken_is_checked_before_daily_limit(store: Store) -> None:
    """둘 다 걸리면 달별이 먼저다 — 내일 다시 해도 안 되는 이유를 알려 준다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(hours=1), dog_id=DOG, month=4))
    store.ai_card_usage.append(_usage(NOW - timedelta(hours=1)))
    with pytest.raises(quota.AiCardMonthTakenError):
        _check(dog_id=DOG, month=4)


def test_same_dog_same_month_failed_is_not_taken(store: Store) -> None:
    store.ai_cards.append(_card("failed", NOW - timedelta(days=3), dog_id=DOG, month=4))
    _check(limit=0, dog_id=DOG, month=4)


def test_same_dog_other_month_is_ok(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, month=4))
    _check(limit=0, dog_id=DOG, month=9)


def test_other_dog_same_month_is_ok(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=OTHER_DOG, month=4))
    _check(limit=0, dog_id=DOG, month=4)


def test_other_owner_same_dog_same_month_is_ok(store: Store) -> None:
    """보호자마다 따로 센다(A안) — 공동 보호자가 같은 강아지로 만든 카드는 내 달을 막지 않는다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), owner=STRANGER, dog_id=DOG, month=4))
    _check(limit=0, dog_id=DOG, month=4)


def test_no_dog_id_skips_month_check(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=None, month=4))
    _check(limit=0, dog_id=None, month=4)


def test_month_args_are_required() -> None:
    """빠뜨려서 달별 검사가 조용히 꺼지면 안 된다."""
    with pytest.raises(TypeError):
        asyncio.run(quota.check_quota(None, OWNER, now=NOW, daily_limit=1))  # type: ignore[call-arg]


# ── 남은 횟수 ───────────────────────────────────────────────────────────


def test_remaining_is_none_when_unlimited(store: Store) -> None:
    assert _remaining(limit=0) is None


def test_remaining_counts_down_and_clamps_at_zero(store: Store) -> None:
    assert _remaining() == 1
    store.ai_card_usage.append(_usage(NOW - timedelta(hours=1)))
    assert _remaining() == 0
    store.ai_card_usage.append(_usage(NOW - timedelta(minutes=30)))
    assert _remaining() == 0
    store.ai_card_usage.append(_usage(KST_YESTERDAY_LAST))
    assert _remaining(limit=3) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_ai_card_quota.py -q`
Expected: FAIL — `ImportError`/`AttributeError` (`AiCardMonthTakenError`, `daily_remaining`, `Store.ai_card_usage` 없음)

- [ ] **Step 3: 리포지토리** — `backend/src/daengs_backend/repositories/ai_card.py`

import 줄 `from daengs_backend.models import AiCard` → `from daengs_backend.models import AiCard, AiCardUsage`.

`count_ready_since` 함수(50~54행)를 **지우고** 그 자리에 넣는다:

```python
async def has_month_card(session: AsyncSession, app_user_id: uuid.UUID, dog_id: uuid.UUID, month: int) -> bool:
    """이 보호자가 이 강아지로 이 달 카드를 이미 갖고 있나 (`ready`·`generating`). **실패는 안 봅니다.**

    보호자마다 따로 봅니다 — 같은 강아지라도 다른 보호자의 카드는 막지 않습니다 (D-077).
    """
    stmt = (
        select(AiCard.id)
        .where(
            AiCard.app_user_id == app_user_id,
            AiCard.dog_id == dog_id,
            AiCard.month == month,
            AiCard.status.in_(("generating", "ready")),
        )
        .limit(1)
    )
    return await session.scalar(stmt) is not None


def add_usage(session: AsyncSession, usage: AiCardUsage) -> AiCardUsage:
    """카드가 `ready` 가 된 기록. 커밋은 부르는 쪽(ready 로 바꾸는 같은 트랜잭션)이 합니다."""
    session.add(usage)
    return usage


async def count_usage_since(session: AsyncSession, app_user_id: uuid.UUID, since: datetime) -> int:
    """`since` 이후의 사용 기록 수. **지운 카드도 셉니다** — 기록은 카드와 따로 남습니다."""
    stmt = select(func.count()).select_from(AiCardUsage).where(
        AiCardUsage.app_user_id == app_user_id, AiCardUsage.used_at >= since
    )
    return int(await session.scalar(stmt) or 0)
```

파일 끝 `delete_all_for_owner` 다음에 추가:

```python
async def delete_usage_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴 정리. ⚠️ `app_users` CASCADE 에 기대면 안 됩니다 — 탈퇴는 그 행을 남깁니다."""
    result = await session.execute(sql_delete(AiCardUsage).where(AiCardUsage.app_user_id == app_user_id))
    return result.rowcount or 0
```

- [ ] **Step 4: 가짜** — `backend/tests/fakes.py`

Store 259행 `self.ai_cards: list = []` 다음에:

```python

        #: AI 카드 사용 기록 (#543, D-077). 카드를 지워도 남습니다 — 가짜 `ai_card_delete` 는 이것을 안 건드립니다.
        self.ai_card_usage: list = []
```

1885~1890행 `ai_card_count_ready_since` 함수를 지우고 그 자리에:

```python
    async def ai_card_has_month_card(session, app_user_id, dog_id, month):
        return any(
            c.app_user_id == app_user_id
            and c.dog_id == dog_id
            and c.month == month
            and c.status in ("generating", "ready")
            for c in store.ai_cards
        )

    def ai_card_add_usage(session, usage):
        store.ai_card_usage.append(usage)
        return usage

    async def ai_card_count_usage_since(session, app_user_id, since):
        return sum(1 for u in store.ai_card_usage if u.app_user_id == app_user_id and u.used_at >= since)
```

`ai_card_delete_all_for_owner` 함수 다음에:

```python
    async def ai_card_delete_usage_for_owner(session, app_user_id):
        mine = [u for u in store.ai_card_usage if u.app_user_id == app_user_id]
        store.ai_card_usage = [u for u in store.ai_card_usage if u.app_user_id != app_user_id]
        return len(mine)
```

monkeypatch 목록에서 `count_ready_since` 줄을 지우고 넣는다:

```python
    monkeypatch.setattr(ai_card_repo, "has_month_card", ai_card_has_month_card)
    monkeypatch.setattr(ai_card_repo, "add_usage", ai_card_add_usage)
    monkeypatch.setattr(ai_card_repo, "count_usage_since", ai_card_count_usage_since)
    monkeypatch.setattr(ai_card_repo, "delete_usage_for_owner", ai_card_delete_usage_for_owner)
```

- [ ] **Step 5: 한도 서비스** — `backend/src/daengs_backend/services/ai_card_quota.py` 전체 교체

```python
"""앱 사용자 AI 카드 생성 한도 (#537 · #543, D-076 · D-077).

**제품 규칙입니다** (사용자 결정 2026-09-15). 부르는 쪽(`services/ai_card.py`)은 세 예외만 압니다.

- 사용자별 **동시 1장** — `AiCardBusyError` (409 `already_generating`)
- **강아지마다 달마다 한 장**, 보호자마다 따로 — 같은 `dog_id`·`month` 의 `ready`/`generating` 카드가
  있으면 `AiCardMonthTakenError` (409 `month_taken`). 그 카드를 지우면 그 달은 다시 열립니다.
  `dog_id` 가 없으면 보지 않습니다.
- KST **하루 N회** (`DAENGS_CARDIMAGE_DAILY_LIMIT`, 기본 1) — `AiCardLimitError` (429 `limit_reached`).
  **`ai_card_usage`(카드가 ready 가 될 때 남는 기록)로 셉니다.** 그래서 카드를 지워도 횟수는 돌아오지
  않고, 실패한 카드는 기록이 없어 세지 않습니다.

실패를 하루 한도에 세지 않는 대신 **모델 호출까지 간 실패**(`PAID_FAILURE_CODES`)는 돈이 나갔으므로
따로 하루 `MAX_PAID_FAILURES_PER_DAY` 번까지만 받습니다. 전체 지출의 바닥은 카드 생성 키의 별도 GCP
프로젝트 지출 상한입니다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.repositories import ai_card as ai_card_repo

KST = ZoneInfo("Asia/Seoul")

# 모델 호출까지 가서(돈이 나간 뒤) 실패한 코드와 그 하루 상한. 설정값으로 빼지 않습니다.
# `interrupted`·`internal`·`unavailable` 은 세지 않습니다.
PAID_FAILURE_CODES = frozenset({"upstream", "no_image", "storage"})
MAX_PAID_FAILURES_PER_DAY = 5


class AiCardBusyError(Exception):
    """이미 만들고 있는 카드가 있습니다. 라우터가 409 `already_generating` 으로 바꿉니다."""


class AiCardMonthTakenError(Exception):
    """이 강아지의 이 달 카드가 이미 있습니다. 라우터가 409 `month_taken` 으로 바꿉니다."""


class AiCardLimitError(Exception):
    """오늘 한도를 다 썼습니다. 라우터가 429 `limit_reached` 로 바꿉니다."""


def stale_after() -> timedelta:
    """`generating` 을 사라진 작업으로 볼 기준. **슬롯을 잡은 시각(`updated_at`)부터** 잰다.

    한 건의 최악은 슬롯을 잡은 뒤 엔진·검수가 각각 `cardimage_timeout_ms` 를 다 쓰고
    재시도까지 하는 경우(2 × 2 × timeout)다. 그보다 짧으면 정상 진행 중인 작업을 실패로
    덮으므로 1분을 더 둔다. **세마포어를 기다리는 대기열 시간은 이 예산 밖이다** —
    `services/ai_card.py::_claim_slot` 이 슬롯을 잡고 돈이 나가는 호출(엔진) 직전에 행을
    다시 보아 `updated_at` 을 그 시각으로 찍으므로, 대기가 길어져도 정리 기준이 그동안
    부풀지 않고, 대기 중에 지워지거나 이미 정리된 행은 애초에 엔진을 부르지 않는다.
    """
    return timedelta(milliseconds=4 * settings.cardimage_timeout_ms) + timedelta(seconds=60)


def kst_day_start(now: datetime) -> datetime:
    return now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)


async def check_quota(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    *,
    now: datetime,
    daily_limit: int,
    dog_id: uuid.UUID | None,
    month: int,
) -> None:
    """돈이 나가기 전에 부릅니다. `dog_id`·`month` 는 **키워드 필수**입니다 — 빠뜨려서 달별 검사가
    조용히 꺼지면 안 됩니다."""
    await ai_card_repo.expire_generating(
        session, app_user_id, stale_before=now - stale_after(), now=now
    )
    if await ai_card_repo.has_generating(session, app_user_id):
        raise AiCardBusyError
    # 하루 한도보다 먼저 — 내일 다시 해도 안 되는 이유이기 때문입니다.
    if dog_id is not None and await ai_card_repo.has_month_card(session, app_user_id, dog_id, month):
        raise AiCardMonthTakenError
    day_start = kst_day_start(now)
    if daily_limit and await ai_card_repo.count_usage_since(session, app_user_id, day_start) >= daily_limit:
        raise AiCardLimitError
    # `daily_limit == 0`(무제한)이어도 적용합니다 — 실패는 하루 한도와 따로 셉니다.
    if (
        await ai_card_repo.count_failed_since(session, app_user_id, day_start, PAID_FAILURE_CODES)
        >= MAX_PAID_FAILURES_PER_DAY
    ):
        raise AiCardLimitError


async def daily_remaining(
    session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime, daily_limit: int
) -> int | None:
    """오늘 남은 횟수. 무제한(`daily_limit == 0`)이면 `None`. 앱이 「오늘 1번 남았어요」를 띄웁니다.

    돈 나간 실패 상한은 여기 반영하지 않습니다 — 그것은 안전장치라 앱에 숫자로 보이지 않습니다.
    """
    if not daily_limit:
        return None
    used = await ai_card_repo.count_usage_since(session, app_user_id, kst_day_start(now))
    return max(0, daily_limit - used)
```

- [ ] **Step 6: 한도 테스트 통과 확인**

Run: `cd backend && uv run pytest tests/test_ai_card_quota.py -q`
Expected: 전부 PASS

- [ ] **Step 7: 서비스 두 자리만 맞춘다** — `backend/src/daengs_backend/services/ai_card.py`

① import 줄 `from daengs_backend.models import AiCard` → `from daengs_backend.models import AiCard, AiCardUsage`

② 124행 `check_quota` 호출:

```python
    await check_quota(
        session, app_user_id, now=now, daily_limit=settings.cardimage_daily_limit, dog_id=dog_id, month=month
    )
```

③ `_finish_ready` 에서 `card.updated_at = datetime.now(UTC)` 와 `await session.commit()` 두 줄을 이렇게 바꾼다:

```python
        now = datetime.now(UTC)
        card.updated_at = now
        # **같은 트랜잭션에서** 사용 기록을 남깁니다 (#543, D-077). 카드를 지워도 이 줄은 남아 하루 한도가
        # 돌아오지 않습니다. ready 가 못 된 카드(실패·중간 삭제)는 여기까지 안 오므로 세지 않습니다.
        ai_card_repo.add_usage(session, AiCardUsage(card_id=card.id, app_user_id=card.app_user_id, used_at=now))
        await session.commit()
```

`backend/.env.example` 229행 주석을 교체:

```
# 앱 사용자 하루 생성 한도 (KST, 카드가 완성될 때 남는 사용 기록으로 셈 — 지워도 안 돌아오고 실패는 안 셈). 0 이면 한도 없음.
```

- [ ] **Step 8: 주변 테스트가 그대로인지**

Run: `cd backend && uv run pytest tests/test_ai_card_quota.py tests/test_ai_card_service.py tests/test_ai_cards_api.py tests/test_ai_card_model.py -q`
Expected: 전부 PASS (`test_ready_card_uses_up_daily_limit`·`test_daily_limit_is_429` 포함 — 이제 ready 때 기록이 남는다). 실패가 있으면 고친다. `count_ready_since` 를 참조하는 곳이 남았는지 `Grep` 으로 `count_ready_since` 를 `backend/` 에서 찾아 0건을 확인한다(`docs/` 의 옛 계획 문서는 건드리지 않는다).

- [ ] **Step 9: 커밋**

```bash
git add backend/src/daengs_backend/repositories/ai_card.py backend/tests/fakes.py \
  backend/src/daengs_backend/services/ai_card_quota.py backend/src/daengs_backend/services/ai_card.py \
  backend/.env.example backend/tests/test_ai_card_quota.py
git commit -m "feat: AI 카드 한도를 사용 기록으로 세고 강아지 달별 한 장을 거른다 (#543)"
```

---

### Task 3: 서비스 — ready 때 기록 · 제목 이름 · 남은 횟수 · 탈퇴 정리

**Files:**
- Modify: `backend/src/daengs_backend/services/ai_card.py` (`start` · `_finish_ready` · `cleanup_for_owner`, 새 함수 `daily_status`, 모듈 docstring 한 줄)
- Test: `backend/tests/test_ai_card_service.py`

**Interfaces:**
- Consumes: `ai_card_repo.add_usage` · `delete_usage_for_owner` (Task 2), `ai_card_quota.daily_remaining` · `AiCardMonthTakenError` (Task 2), `models.AiCardUsage` (Task 1)
- Produces:
  - `async service.start(session, app_user_id, *, photo, content_type, month, dog_name, dog_id, title_name: str | None = None, now=None) -> AiCard`
  - `async service.daily_status(session, app_user_id, *, now: datetime | None = None) -> tuple[int | None, int | None]` — `(daily_limit, daily_remaining)`, 무제한이면 `(None, None)`
  - `cleanup_for_owner` 가 사용 기록도 지운다 (반환값은 **지운 카드 수 그대로**)

- [ ] **Step 1: 실패하는 테스트** — `backend/tests/test_ai_card_service.py`

import 줄 `from daengs_backend.models import AiCard` → `from daengs_backend.models import AiCard, AiCardUsage` (타입 확인용).

파일 끝에 추가:

```python
# ── #543 제품 규칙 ──────────────────────────────────────────────────────


def test_ready_records_usage(store, jobs) -> None:
    card = _start()
    assert store.ai_card_usage == []  # 시작만으로는 안 센다
    _run_all(jobs)
    assert len(store.ai_card_usage) == 1
    usage = store.ai_card_usage[0]
    assert isinstance(usage, AiCardUsage)
    assert usage.card_id == card.id and usage.app_user_id == OWNER and usage.used_at is not None


def test_failed_records_no_usage(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine(error=EngineError("upstream", "x")))
    _start()
    _run_all(jobs)
    assert store.ai_card_usage == []


def test_row_deleted_mid_generation_records_no_usage(store, jobs, monkeypatch) -> None:
    card = _start()
    monkeypatch.setattr(
        ai_card_engine, "default_engine", lambda: _SideEffectEngine(lambda: store.ai_cards.remove(card))
    )
    _run_all(jobs)
    assert store.ai_card_usage == []


def test_deleting_ready_card_does_not_give_limit_back(store, jobs) -> None:
    card = _start()
    _run_all(jobs)
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    assert len(store.ai_card_usage) == 1  # 기록은 남는다
    with pytest.raises(quota.AiCardLimitError):
        _start()


def test_same_dog_same_month_is_taken_other_month_is_ok(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    _start(dog_id=pet.id, month=4)
    _run_all(jobs)
    with pytest.raises(quota.AiCardMonthTakenError):
        _start(dog_id=pet.id, month=4)
    assert _start(dog_id=pet.id, month=9).status == "generating"


def test_other_dog_same_month_is_ok(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    first = FakePet(app_user_id=OWNER, name="첫째", breed="mix")
    second = FakePet(app_user_id=OWNER, name="둘째", breed="mix")
    store.pets.extend([first, second])
    _start(dog_id=first.id, month=4)
    _run_all(jobs)
    assert _start(dog_id=second.id, month=4).status == "generating"


def test_deleting_month_card_reopens_month(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    card = _start(dog_id=pet.id, month=4)
    _run_all(jobs)
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    assert _start(dog_id=pet.id, month=4).status == "generating"


def test_title_name_changes_title_only(store, jobs) -> None:
    card = _start(title_name="  KONG   CHAN ")
    assert card.title == "BLOSSOM KONG CHAN"
    assert card.dog_name == "네오"


def test_blank_title_name_falls_back_to_dog_name(store, jobs) -> None:
    assert _start(title_name="   ").title == "BLOSSOM 네오"


def test_title_name_is_uppercased(store, jobs) -> None:
    assert _start(title_name="kong").title == "BLOSSOM KONG"


def test_long_uppercased_title_name_is_clamped(store, jobs) -> None:
    """`ß`.upper() 는 `SS` — 40자 `title_name` 의 제목도 VARCHAR(80) 을 넘지 않게 자른다."""
    assert len(_start(title_name="ß" * 40).title) <= 80


def test_daily_status(store, jobs, monkeypatch) -> None:
    assert asyncio.run(service.daily_status(FakeSession(), OWNER)) == (1, 1)
    _start()
    _run_all(jobs)
    assert asyncio.run(service.daily_status(FakeSession(), OWNER)) == (1, 0)
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    assert asyncio.run(service.daily_status(FakeSession(), OWNER)) == (None, None)


def test_cleanup_for_owner_removes_usage(store, jobs) -> None:
    card = _start()
    _run_all(jobs)
    store.ai_card_usage.append(AiCardUsage(card_id=uuid.uuid4(), app_user_id=STRANGER, used_at=datetime.now(UTC)))
    assert asyncio.run(service.cleanup_for_owner(FakeSession(), OWNER)) == 1
    assert [u.app_user_id for u in store.ai_card_usage] == [STRANGER]
    assert card not in store.ai_cards
```

⚠ 한 테스트 안에서 `_start` 를 두 번 부르면 첫 카드가 `generating` 으로 남아 두 번째가 `AiCardBusyError` 가 된다 — 두 번째 전에 `_run_all(jobs)` 로 끝내야 한다(위 테스트들은 그렇게 돼 있다).

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_ai_card_service.py -q`
Expected: `title_name`·`daily_status`·사용 기록 삭제 관련 새 테스트 FAIL (`TypeError: unexpected keyword 'title_name'`, `AttributeError: daily_status`). 사용 기록을 남기는 테스트(`test_ready_records_usage` 등)는 Task 2 가 기록을 이미 넣었으므로 PASS 여도 정상.

- [ ] **Step 3: 구현** — `backend/src/daengs_backend/services/ai_card.py`

(`AiCardUsage` import 와 `_finish_ready` 의 기록 한 줄은 Task 2 가 이미 넣었다 — 다시 넣지 않는다.)

import 줄 교체:

```python
from daengs_backend.services.ai_card_quota import AiCardBusyError, check_quota, daily_remaining, stale_after
```

모듈 docstring 3~4행의 한도 설명 뒤(「…남의 강아지·한도)을 전부 동기로 거른 뒤」 문장은 그대로 두고) 첫 단락 끝에 한 문장 추가:

```
한도 규칙은 `services/ai_card_quota.py` 가 정하고(D-077), 여기서는 카드가 `ready` 가 되는 같은
트랜잭션에서 사용 기록(`ai_card_usage`)을 남깁니다.
```

`start` 시그니처에 `dog_id` 다음으로 `title_name: str | None = None,` 을 추가하고, `name = " ".join(dog_name.split())` 다음 줄에:

```python
    # 제목에만 쓰는 이름 (#543). 비면 `dog_name` 그대로 — `dog_name` 은 늘 그대로 저장합니다.
    title_source = " ".join((title_name or "").split()) or name
```

`title=title_text(meta.card_name, name)[:_TITLE_MAX],` → `title=title_text(meta.card_name, title_source)[:_TITLE_MAX],`

`list_cards` 다음에 추가:

```python
async def daily_status(
    session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[int | None, int | None]:
    """`(daily_limit, daily_remaining)`. 무제한이면 `(None, None)` — 앱이 막을지 정하는 값입니다."""
    limit = settings.cardimage_daily_limit
    remaining = await daily_remaining(session, app_user_id, now=now or datetime.now(UTC), daily_limit=limit)
    return (limit or None, remaining)
```

`cleanup_for_owner` 의 마지막 줄 `return await ai_card_repo.delete_all_for_owner(session, app_user_id)` 를:

```python
    deleted = await ai_card_repo.delete_all_for_owner(session, app_user_id)
    # 사용 기록도 명시로 지웁니다 — 카드와 FK 로 안 묶였고, app_users CASCADE 는 탈퇴에서 안 돕니다.
    await ai_card_repo.delete_usage_for_owner(session, app_user_id)
    return deleted
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_ai_card_service.py tests/test_ai_card_quota.py -q`
Expected: 전부 PASS (`test_ready_card_uses_up_daily_limit` 포함)

- [ ] **Step 5: 탈퇴 경로 테스트가 그대로인지**

Run: `cd backend && uv run pytest tests/ -q -k "withdraw or app_auth or ai_card" -p no:cacheprovider`
Expected: PASS. 실패하면 가짜 `delete_usage_for_owner` 가 install 됐는지부터 본다.

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_backend/services/ai_card.py backend/tests/test_ai_card_service.py
git commit -m "feat: AI 카드가 ready 될 때 사용 기록을 남기고 제목 이름·남은 횟수를 준다 (#543)"
```

---

### Task 4: HTTP 경계 — `title_name` · `409 month_taken` · 목록의 남은 횟수

**Files:**
- Modify: `backend/src/daengs_backend/routers/ai_card.py`
- Modify: `backend/src/daengs_backend/schemas/ai_card.py`
- Test: `backend/tests/test_ai_cards_api.py`

**Interfaces:**
- Consumes: `service.start(..., title_name=)` · `service.daily_status` (Task 3), `ai_card_quota.AiCardMonthTakenError` (Task 2)
- Produces (앱 계약):
  - `POST /app/ai-cards?month=&dog_name=&dog_id=&title_name=` — `title_name` 선택, 최대 40자
  - 409 `{"code": "month_taken", "message": "네오는 이미 4월 카드가 있어요."}`
  - `GET /app/ai-cards` → `{"cards": [...], "daily_limit": 1, "daily_remaining": 0}` (무제한이면 둘 다 `null`)

- [ ] **Step 1: 실패하는 테스트** — `backend/tests/test_ai_cards_api.py`

import 줄 `from fakes import FakeAdmin, FakeAppUser, FakeSession, Store, install` → `from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install`

파일 끝에 추가:

```python
# ── #543 제품 규칙 ──────────────────────────────────────────────────────


def test_title_name_goes_to_title_only(client: TestClient) -> None:
    r = _post(client, title_name="kong")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["title"] == "BLOSSOM KONG" and body["dog_name"] == "네오"


def test_title_name_too_long_is_422(client: TestClient) -> None:
    assert _post(client, title_name="a" * 41).status_code == 422


def test_month_taken_is_409_with_dog_name(
    client: TestClient, store: Store, jobs: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    pet = FakePet(app_user_id=OWNER, name="네오", breed="mix")
    store.pets.append(pet)
    assert _post(client, dog_id=str(pet.id)).status_code == 202
    _run_all(jobs)
    r = _post(client, dog_id=str(pet.id))
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "month_taken", "message": "네오는 이미 4월 카드가 있어요."}


def test_delete_does_not_give_limit_back(client: TestClient, jobs: list) -> None:
    card_id = _post(client).json()["id"]
    _run_all(jobs)
    assert client.delete(f"/app/ai-cards/{card_id}").status_code == 204
    r = _post(client)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "limit_reached"


def test_list_has_daily_remaining(client: TestClient, jobs: list) -> None:
    before = client.get("/app/ai-cards").json()
    assert (before["daily_limit"], before["daily_remaining"]) == (1, 1)
    _post(client)
    _run_all(jobs)
    after = client.get("/app/ai-cards").json()
    assert (after["daily_limit"], after["daily_remaining"]) == (1, 0)


def test_list_unlimited_is_null(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    body = client.get("/app/ai-cards").json()
    assert body["daily_limit"] is None and body["daily_remaining"] is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [("네오", "네오는"), ("콩", "콩은"), ("KONG", "KONG은(는)"), ("보리 2", "보리 2은(는)")],
)
def test_with_topic(name: str, expected: str) -> None:
    assert ai_card_router._with_topic(name) == expected
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_ai_cards_api.py -q`
Expected: 새 테스트 FAIL (`title_name` 무시·`month_taken` 이 500·`daily_limit` 키 없음·`_with_topic` 없음)

- [ ] **Step 3: 스키마** — `backend/src/daengs_backend/schemas/ai_card.py` 의 `AiCardListResponse` 교체

```python
class AiCardListResponse(BaseModel):
    cards: list[AiCardResponse]
    #: 하루 한도와 오늘 남은 횟수 (#543, D-077). 무제한이면 둘 다 `null`. 앱이 「오늘 1번 남았어요」를 띄우고
    #: 0 이면 막는다 — 이 칸이 없는 옛 서버에서는 앱이 막지 않고 서버 429/409 문장에 맡긴다.
    daily_limit: int | None = None
    daily_remaining: int | None = None
```

- [ ] **Step 4: 라우터** — `backend/src/daengs_backend/routers/ai_card.py`

import 줄 교체:

```python
from daengs_backend.services.ai_card_quota import AiCardBusyError, AiCardLimitError, AiCardMonthTakenError
```

`_not_found` 다음에 추가:

```python
def _with_topic(name: str) -> str:
    """이름 뒤에 은/는. 마지막 글자가 한글 음절이 아니면(영문·숫자) 받침을 모르므로 `은(는)`."""
    last = name[-1]
    if "가" <= last <= "힣":
        return name + ("은" if (ord(last) - ord("가")) % 28 else "는")
    return name + "은(는)"
```

`create_card` 시그니처의 `dog_id: uuid.UUID | None = None,` 다음에:

```python
    title_name: Annotated[str | None, Query(max_length=40)] = None,
```

docstring 첫 줄 다음에 한 줄 추가: `` `title_name` 은 제목에만 씁니다 — 비우면 `dog_name` (#543). ``

`ai_card_service.start(` 호출 인자에 `dog_id=dog_id,` 다음 줄로 `title_name=title_name,` 추가.

`except AiCardBusyError:` 블록 **다음**, `except AiCardLimitError:` **앞**에:

```python
    except AiCardMonthTakenError:
        name = " ".join(dog_name.split())
        raise _error(
            status.HTTP_409_CONFLICT, "month_taken", f"{_with_topic(name)} 이미 {month}월 카드가 있어요."
        ) from None
```

`list_cards` 본문 교체:

```python
    cards = await ai_card_service.list_cards(session, user.app_user_id)
    daily_limit, daily_remaining = await ai_card_service.daily_status(session, user.app_user_id)
    return AiCardListResponse(
        cards=[_to_response(c) for c in cards], daily_limit=daily_limit, daily_remaining=daily_remaining
    )
```

docstring 을 `"""내 카드 전부, 최근 것부터 + 오늘 남은 횟수. **이미지 주소는 안 싣습니다** — 필요한 것만 단건 조회합니다."""` 로.

- [ ] **Step 5: 통과 확인**

Run: `cd backend && uv run pytest tests/test_ai_cards_api.py tests/test_ai_card_service.py tests/test_ai_card_quota.py tests/test_ai_card_model.py -q`
Expected: 전부 PASS

- [ ] **Step 6: 린트**

Run: `cd backend && uv run ruff check src/daengs_backend/routers/ai_card.py src/daengs_backend/schemas/ai_card.py src/daengs_backend/services/ai_card.py src/daengs_backend/services/ai_card_quota.py src/daengs_backend/repositories/ai_card.py src/daengs_backend/models/ai_card.py tests/test_ai_cards_api.py tests/test_ai_card_service.py tests/test_ai_card_quota.py tests/test_ai_card_model.py tests/fakes.py`
Expected: `All checks passed!` (고칠 게 있으면 고친다 — 기존 파일의 옛 경고는 건드리지 않는다)

- [ ] **Step 7: 커밋**

```bash
git add backend/src/daengs_backend/routers/ai_card.py backend/src/daengs_backend/schemas/ai_card.py \
  backend/tests/test_ai_cards_api.py
git commit -m "feat: /app/ai-cards 에 제목 이름·409 month_taken·목록의 남은 횟수 (#543)"
```

(Step 6 에서 다른 파일을 고쳤다면 그 경로도 `git add` 에 넣는다.)

---

### Task 5: 문서 — D-077 · cardimage README · 설계 §5 · worklog

**Files:**
- Modify: `docs/decisions.md` (파일 끝에 D-077 추가, D-076 「한도는 테스트 단계용」 단락 끝에 한 줄)
- Modify: `docs/cardimage/README.md` (「지금 상태」 첫 단락 · 「정해진 것」 표 · 「안 정해진 것」)
- Modify: `docs/cardimage/spec-2026-09-14-app-ai-cards.md` (§5 끝에 교체 절)
- Modify: `docs/cardimage/worklog.md` (한 절 추가 — 기존 절 형식을 먼저 읽고 따른다)

**Interfaces:**
- Consumes: Task 1~4 의 이름 (`ai_card_usage` · `AiCardMonthTakenError` · `month_taken` · `title_name` · `daily_limit`/`daily_remaining` · `daily_status`)

- [ ] **Step 1: `docs/decisions.md` 파일 끝(D-076 뒤)에 추가** — 끝이 D-076 인지 `Grep "^## D-07"` 으로 먼저 확인한다(D-077 이 이미 있으면 멈추고 컨트롤러에게 알린다).

```markdown

## D-077
### AI 카드 한도는 지워도 안 돌아오는 사용 기록으로 세고, 강아지마다 달마다 한 장

2026-09-15, #543. D-076 의 「한도는 테스트 단계용」을 사용자가 정한 제품 규칙으로 바꾼다. 앱(`SAJOYO/DAENGS_APP#414`)
에서 실기기로 써 보니 9월 카드를 만들고 지운 뒤 4월 카드가 또 만들어졌다 — 한도가 **남아 있는** `ready` 행을 셌기 때문이다.

| 규칙 | 결정 |
| --- | --- |
| 하루 한도 | KST 하루 1회(`DAENGS_CARDIMAGE_DAILY_LIMIT`, 0 이면 무제한). 누끼 카드와 따로 센다 |
| 지우기 | 지워도 횟수는 돌아오지 않는다 |
| 실패 | 서버가 그리다 실패한 카드는 세지 않는다. 돈 나간 실패 하루 5번 상한은 그대로 |
| 달별 | 강아지마다 달마다 한 장. **보호자마다 따로** 센다(카드는 만든 사람 것이고 도감도 보호자마다 따로다) |
| 제목 이름 | 요청의 `title_name` 은 제목에만 쓴다. 비우면 `dog_name` |
| 남은 횟수 | `GET /app/ai-cards` 의 `daily_limit`·`daily_remaining` |

**세는 곳은 표 `ai_card_usage`.** 카드가 `ready` 가 되는 트랜잭션에서 한 줄 남기고, `card_id` 에는 **FK 를 걸지
않는다** — 걸면 CASCADE 는 횟수를 돌려주고 RESTRICT 는 삭제를 막는다. 카드 삭제는 저장소 객체와 행을 둘 다
지우므로(soft delete 아님) `ai_cards` 만으로는 지운 카드를 셀 수 없다. `ai_cards` 에 soft delete 를 넣는 안은
목록·조회·bridge·탈퇴 정리 쿼리를 전부 바꿔야 해서 기각했다. 탈퇴는 `app_users` 행을 남기므로 사용 기록도
`cleanup_for_owner` 가 명시로 지운다. 배포할 때 마이그레이션이 남아 있는 `ready` 카드를 백필한다 — 안 하면
배포 당일 이미 만든 사람이 한 장 더 만든다.

**달별 한 장은 살아 있는 카드로 거른다** (`ready`·`generating`, 같은 `app_user_id`·`dog_id`·`month`). 그래서
그 달 카드를 **지우면 그 달은 다시 열린다** — 하루 한도만 안 돌아온다. `ai_cards.month` 는 1~12 이고 연도가
없어 "그 달"은 테마 달이다(배지가 `26JAN`…으로 틀에 구워져 연도가 고정인 것과 같다). `dog_id` 없이 만든 카드와
강아지를 지워 `dog_id` 가 `NULL` 이 된 카드는 달별 검사에 안 걸린다. 검사는 돈이 나가기 전(`start` 의 동기 구간),
동시 1장 다음·하루 한도 앞이다 — 내일 다시 해도 안 되는 이유가 먼저 보이게.

**하루의 경계는 `ready` 가 된 시각**(`used_at`)이다. 23:59 에 시작해 00:00 에 끝난 카드는 다음 날 한 번으로 센다.

되돌리기: 규칙은 여전히 `services/ai_card_quota.py::check_quota` 한 곳이다. 표를 버리려면 `check_quota` 를
`ai_cards` 로 되돌리고 `_finish_ready` 의 기록 한 줄을 지운다.
```

D-076 의 「한도는 테스트 단계용.」 단락 끝(「…`check_quota` 를 통째로 바꾼다.」) 바로 다음 줄에 `→ **D-077 (2026-09-15, #543) 로 교체됐다.**` 한 줄을 넣는다.

- [ ] **Step 2: `docs/cardimage/README.md`**

「지금 상태」 첫 단락(앱 사용자 경로)의 문장 `한도는 사용자별 동시 1장 + 하루 완성 1장(\`DAENGS_CARDIMAGE_DAILY_LIMIT\`).` 를 다음으로 바꾼다:

```
한도는 **제품 규칙(09-15, #543 · D-077)** — 동시 1장 · 하루 1회(카드가 완성될 때 남는 `ai_card_usage` 로 세서 지워도 안 돌아옴, 실패는 안 셈) · 강아지마다 달마다 한 장(보호자마다 따로). 요청의 `title_name` 은 제목에만 쓰고, 목록 응답에 `daily_limit`·`daily_remaining` 이 실린다. **배포 전에** `db/migrations/2026-09-15_ai_card_usage.sql` 도 적용한다.
```

「정해진 것」 표의 `| 앱 경로 |` 행 다음에 행 추가:

```
| 한도 규칙 | 동시 1장 · KST 하루 1회(사용 기록 `ai_card_usage` — 지워도 안 돌아옴, 실패는 안 셈) · 강아지마다 달마다 한 장(보호자마다 따로, 지우면 그 달은 다시 열림) · 제목에만 쓰는 `title_name` · 목록의 남은 횟수 — **D-077** | 사용자 결정 09-15 (#543). 앱 짝은 `DAENGS_APP#414` |
```

「앱 경로」 행의 `사용자별 동시 1장 + 하루 완성 1장` 은 `한도는 아래 「한도 규칙」` 으로 바꾼다.

「안 정해진 것」의 `- **제품 규칙** — …` 줄을 다음으로 교체:

```
- ~~**제품 규칙** — 카드를 몇 장·어떤 조건으로 줄지~~ → **D-077 로 확정 (09-15, #543).** 활동 보상·유료는 여전히 안 정했다.
```

- [ ] **Step 3: `docs/cardimage/spec-2026-09-14-app-ai-cards.md`** — §5 의 마지막 불릿(「관리자 경로 … 한도를 받지 않는다」) 다음에 추가

```markdown

### 2026-09-15 제품 규칙으로 교체 (#543, D-077)

위 규칙은 테스트 단계용이었고 `check_quota` 를 통째로 바꿨다. 지금 규칙:

- 동시 1장 → `409 already_generating` (그대로)
- 같은 `app_user_id`·`dog_id`·`month` 에 `ready`/`generating` 카드가 있으면 `AiCardMonthTakenError` → `409 month_taken`
  「{이름}은(는) 이미 {달}월 카드가 있어요.」. `dog_id` 가 없으면 안 본다. 동시 1장 다음·하루 한도 앞.
- KST 오늘 **`ai_card_usage`** 줄 수 ≥ `daily_limit` 이면 `429 limit_reached`. 줄은 `_finish_ready` 가 `ready` 로 바꾸는
  같은 트랜잭션에서 남기고, 카드를 지워도 남는다(`card_id` FK 없음). 표는 `db/init/39_ai_card_usage.sql`.
- 돈 나간 실패 하루 5번 → `429` (그대로)
- `POST` 쿼리 `title_name`(선택, 40자) — 제목에만. `GET /app/ai-cards` 에 `daily_limit`·`daily_remaining`(무제한이면 `null`).
- 탈퇴 정리(`cleanup_for_owner`)가 사용 기록도 지운다.
```

- [ ] **Step 4: `docs/cardimage/worklog.md`** — 먼저 파일 머리와 마지막 절을 읽고 같은 형식(제목 수준·날짜 표기)으로 한 절을 **맨 끝(또는 기존 절들이 최신이 위라면 맨 위)** 에 추가한다. 내용:

```
## 2026-09-15 — #543 한도 제품 규칙

- 계기: 앱(DAENGS_APP#414) 실기기에서 9월 카드를 만들고 지운 뒤 4월 카드가 또 만들어짐 — 한도가 남아 있는 ready 행을 셈.
- 사용자 결정: 하루 1회(지워도 안 돌아옴·실패 안 셈) · 강아지마다 달마다 한 장(보호자마다 따로) · 제목에만 쓰는 이름 · 목록의 남은 횟수. D-077.
- 구현: 표 `ai_card_usage`(카드 ready 때 한 줄, card_id FK 없음, 기존 ready 백필) · `check_quota` 교체(`AiCardMonthTakenError`) · `title_name` · `daily_limit`/`daily_remaining` · 탈퇴 정리. 계획 `plan-2026-09-15-ai-card-quota-rules.md`.
- 확인한 것: `ai_cards.month` 는 연도가 없어 달별 한 장은 테마 달 기준 — 그 달 카드를 지우면 그 달은 다시 열린다.
- 배포: `db/migrations/2026-09-15_ai_card_usage.sql` 을 코드보다 먼저 개발서버·GCP 에 적용.
```

- [ ] **Step 5: 인용 자리 grep** (collaboration.md §4 ⓐ) — `Grep` 으로 `docs/` 와 `CLAUDE.md` 에서 `하루 완성 1장` · `테스트 단계용` · `count_ready_since` 를 찾아, 지금 규칙을 **현재형으로 설명하는** 자리가 남았으면 고친다. 옛 계획 문서(`plan-2026-09-14-*.md`)와 D-076 본문(Step 1 에서 교체 한 줄만 붙임)은 기록이라 고치지 않는다.

- [ ] **Step 6: 커밋**

```bash
git add docs/decisions.md docs/cardimage/README.md docs/cardimage/spec-2026-09-14-app-ai-cards.md docs/cardimage/worklog.md
git commit -m "docs: AI 카드 한도 제품 규칙을 D-077 로 기록한다 (#543)"
```

---

## 컨트롤러 마무리 (태스크 뒤, 에이전트 아님)

1. 저장소 루트에서 `cd backend && uv run check`.
2. 전체 `uv run pytest` — 분리 프로세스로(`Start-Process cmd /c "uv run pytest -q > <scratchpad>/pytest.log 2>&1 & echo exit=done >> <scratchpad>/pytest.log"` + Monitor). 다른 에이전트가 안 돌 때만.
3. Task 1 Step 10 하네스 결과를 PR 본문에 적는다.
4. PR #543 본문: 「작업 목록」·「확인한 것」 체크, 「배포 영향」의 필요한 조치에 마이그레이션 파일 이름과 **백필함**을 적는다. 컨텍스트 메모에 D-077 과 하루 경계(`used_at`) 한 줄.
5. `superpowers:finishing-a-development-branch` 는 쓰지 않는다 — 머지·draft 해제는 사람이 정한다.
