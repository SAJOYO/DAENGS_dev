-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- ⚠️ **이 파일은 뒤 마이그레이션이 넓힌 것을 막으면 안 된다.**
-- `2026-08-30_crawl_runs_trigger_revision.sql` 이 `trigger` CHECK 에 `'revision'` 을 더한다.
-- 그래서 여기서는 **값의 개수를 세지 않고** `due`·`manual` 이 들어 있는지만 본다 —
-- 개수를 세면 이 verify 가 다음 날짜 마이그레이션을 적용한 DB 전부에서 실패한다.
-- (`verify_<옛것>` 은 `<새것>` 까지 적용된 DB 위에서도 돈다는 것이 이 폴더의 성질이다.)
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('crawl_runs') IS NULL THEN
        RAISE EXCEPTION 'missing table: crawl_runs';
    END IF;
    relation := to_regclass('crawl_runs');

    FOR item IN SELECT * FROM (VALUES
        ('id', 'bigint', 'true'),
        -- **`run_id` 는 nullable 이다.** 파일 끝에서 일부러 NOT NULL 을 푼다 — 행은
        -- 수집이 시작될 때 `status='running'` 으로 먼저 들어가고, 이 값은 끝나야 나온다.
        -- NULL = 아직 안 끝났거나 끝나기 전에 죽었다는 뜻이라, 그 상태를 못 적으면
        -- 워커가 죽은 실행을 아예 기록할 수 없다.
        ('run_id', 'text', 'false'),
        ('source_id', 'text', 'true'),
        ('trigger', 'text', 'true'),
        ('status', 'text', 'true'),
        ('docs_fetched', 'integer', 'true'),
        ('docs_changed', 'integer', 'true'),
        ('docs_failed', 'integer', 'true'),
        ('docs_skipped', 'integer', 'true'),
        -- C3(개정 감지)의 입력이다. NOT NULL + 빈 배열 기본값이라 "안 적음"과 "없음"이 같다.
        ('changed_slugs', 'text[]', 'true'),
        ('error', 'text', 'false'),
        ('started_at', 'timestamp with time zone', 'true'),
        ('finished_at', 'timestamp with time zone', 'false')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: crawl_runs.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- `changed_slugs` 의 기본값. 없으면 INSERT 마다 명시해야 하고, 빠뜨린 행은 NULL 이 되어
    -- 개정 감지가 "바뀐 것이 없다"와 "안 적었다"를 못 가른다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attrdef d
        JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
        WHERE d.adrelid = relation AND a.attname = 'changed_slugs'
          AND pg_get_expr(d.adbin, d.adrelid) LIKE '%{}%'
    ) THEN
        RAISE EXCEPTION 'column mismatch: crawl_runs.changed_slugs must default to an empty array';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.contype = 'p' AND c.convalidated
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: crawl_runs has no primary key';
    END IF;

    -- `status` 는 **네 값 전부**여야 한다. `unavailable` 을 `failed` 와 가르는 것이 이 칸의
    -- 이유다 — 키 미설정·시드 URL 사망은 실패가 아니라 "아직 못 하는 것"이고, 화면에서
    -- 다르게 보여야 사람이 고칠 것을 안다. `running` 이 빠지면 시작 시점의 행을 못 넣는다.
    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = relation AND c.conname = 'crawl_runs_status_check'
      AND c.contype = 'c' AND c.convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'constraint mismatch: crawl_runs_status_check missing or not validated';
    END IF;
    FOR item IN SELECT * FROM (VALUES
        ('running'), ('ok'), ('failed'), ('unavailable')
    ) AS expected(value) LOOP
        IF position('''' || item.value || '''' IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: crawl_runs_status_check lost %, got %',
                item.value, definition;
        END IF;
    END LOOP;

    -- `trigger` 는 **들어 있는지만** 본다 (위 머리말 참고 — 다음 장이 넓힌다).
    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = relation AND c.conname = 'crawl_runs_trigger_check'
      AND c.contype = 'c' AND c.convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'constraint mismatch: crawl_runs_trigger_check missing or not validated';
    END IF;
    FOR item IN SELECT * FROM (VALUES ('due'), ('manual')) AS expected(value) LOOP
        IF position('''' || item.value || '''' IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: crawl_runs_trigger_check lost %, got %',
                item.value, definition;
        END IF;
    END LOOP;

    -- 인덱스 둘. 관리자 화면의 기본 질의가 "소스별 최신 실행"이라 (source_id, started_at DESC) 다.
    FOR item IN SELECT * FROM (VALUES
        ('idx_crawl_runs_source_started', 'started_at DESC'),
        ('idx_crawl_runs_run_id', NULL)
    ) AS expected(index_name, fragment) LOOP
        SELECT pg_get_indexdef(i.indexrelid) INTO definition
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = relation AND c.relname = item.index_name
          AND i.indisvalid AND i.indisready;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'index mismatch: % missing or invalid', item.index_name;
        END IF;
        -- 정렬 방향이 뒤집히면 "최신 순" 질의가 인덱스를 거꾸로 읽는다. 이름은 그대로다.
        IF item.fragment IS NOT NULL AND position(item.fragment IN definition) = 0 THEN
            RAISE EXCEPTION 'index mismatch: % lost %, got %',
                item.index_name, item.fragment, definition;
        END IF;
    END LOOP;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 상태별 실행 수. `running` 이 오래 남아 있으면 워커가 죽은 것이다 (C5 의 "잔존 행").
SELECT status, count(*) AS runs, max(started_at) AS latest
FROM crawl_runs
GROUP BY status
ORDER BY runs DESC;
