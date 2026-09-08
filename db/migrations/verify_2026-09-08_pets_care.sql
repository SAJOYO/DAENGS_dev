-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다. `db-migrate.yml` 도 종료 코드로 성공을 판정하므로
-- SELECT 만 있으면 **틀려도 통과한다.** 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
DO $verify$
DECLARE
    relation regclass;
    default_expression text;
    col record;
BEGIN
    IF to_regclass('pets') IS NULL THEN
        RAISE EXCEPTION 'missing table: pets';
    END IF;
    relation := to_regclass('pets');

    -- ① 칸 넷이 생겼나. 타입이 맞고 **전부 NULL 을 허용해야 한다** — NULL 이 '모름'이다.
    FOR col IN
        SELECT * FROM (VALUES
            ('feeding_style',     'character varying(10)'),
            ('feeding_times',     'jsonb'),
            ('health_conditions', 'character varying(200)'),
            ('medications',       'character varying(200)')
        ) AS want(name, type)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = col.name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = col.type
              AND NOT a.attnotnull
        ) THEN
            RAISE EXCEPTION 'column mismatch: pets.% (want %, nullable)', col.name, col.type;
        END IF;

        -- ② **기본값이 없어야 한다.** `DEFAULT 'free'` 는 타입도 널 허용도 안 건드려 ① 을
        --    통과하는데, 그 순간 안 물어본 아이가 전부 자율급식이 된다 (`registered` 와 같은 규칙).
        SELECT pg_get_expr(d.adbin, d.adrelid) INTO default_expression
        FROM pg_attrdef d
        JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
        WHERE d.adrelid = relation AND a.attname = col.name;
        IF default_expression IS NOT NULL THEN
            RAISE EXCEPTION 'column mismatch: pets.% must have no default, got %',
                col.name, default_expression;
        END IF;
    END LOOP;

    -- ③ 제약 셋. 이름으로 찾는다 — 없으면 자율급식에 시각이 붙거나 객체가 들어온다.
    FOR col IN
        SELECT * FROM (VALUES
            ('pets_feeding_style_check'),
            ('pets_feeding_times_need_schedule'),
            ('pets_feeding_times_array')
        ) AS want(name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = col.name AND c.contype = 'c'
        ) THEN
            RAISE EXCEPTION 'missing check constraint: pets.%', col.name;
        END IF;
    END LOOP;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 기존 행은 **전부 NULL 이어야 한다** — 백필할 정답이 없어서 안 채웠다.
-- 앱 입력 화면(DAENGS_APP#200)이 생기면 그때부터 사람이 답한 값만 들어온다.
SELECT count(*) AS pets_total,
       count(feeding_style) AS feeding_answered,
       count(health_conditions) AS conditions_answered,
       count(medications) AS medications_answered
FROM pets;
