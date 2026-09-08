-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- 이 마이그레이션은 한 줄짜리다 — `trigger` CHECK 에 `'revision'` 을 더한다. 그런데 안 걸면
-- **아무것도 안 깨지고 화면에서만 사라진다**: Beat 가 개정 판정으로 깨운 수집의 기록이
-- CHECK 에 막히는데, 기록 실패는 크롤을 죽이지 않는 설계라(RAG-047 ②) **수집은 정상으로
-- 끝나고 관리자 화면에만 그 실행이 안 보인다.** 그래서 이 한 줄에 verify 가 필요하다.
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

    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = relation AND c.conname = 'crawl_runs_trigger_check'
      AND c.contype = 'c' AND c.convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'constraint mismatch: crawl_runs_trigger_check missing or not validated';
    END IF;

    -- **셋 다 있어야 한다.** 마이그레이션이 DROP → ADD 로 바꿔 다는 모양이라, 다시 다는 쪽을
    -- 빠뜨리면 제약 자체가 사라진다 — 그러면 `trigger` 에 무엇이든 들어간다.
    FOR item IN SELECT * FROM (VALUES
        ('due'), ('manual'), ('revision')
    ) AS expected(value) LOOP
        IF position('''' || item.value || '''' IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: crawl_runs_trigger_check lost %, got %',
                item.value, definition;
        END IF;
    END LOOP;

    -- 제약이 **살아 있어야** 한다 — 값 셋이 다 보여도 `NOT VALID` 면 새 행을 안 막는다.
    -- (`convalidated` 를 위에서 이미 걸었으므로 여기서는 컬럼만 다시 확인한다.)
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = 'crawl_runs_trigger_check'
          AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['trigger']
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: crawl_runs_trigger_check is not on column trigger';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 트리거별 실행 수. `revision` 이 0이면 아직 개정 판정으로 깨운 수집이 없었던 것이고,
-- 그 자체는 정상이다 (이 제약은 그때가 왔을 때 기록이 남게 하는 준비다).
SELECT trigger, count(*) AS runs, max(started_at) AS latest
FROM crawl_runs
GROUP BY trigger
ORDER BY runs DESC;
