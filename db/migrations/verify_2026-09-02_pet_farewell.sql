-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
-- (2026-09-07 #295 에 단언형으로 다시 썼다. 그 전에는 SELECT 나열이라 **틀려도 녹색**이었다.)
--
-- 배웅한 날 한 칸. 아이를 떠나보낸 사람에게 선택지가 삭제뿐이던 것을 고친 자리라
-- (`DAENGS_APP#76`) **행을 안 지우고 날짜만 채운다** — 그래야 아이가 목록에 남고
-- 함께한 산책과 카드도 남는다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    default_expression text;
BEGIN
    IF to_regclass('pets') IS NULL THEN
        RAISE EXCEPTION 'missing table: pets';
    END IF;
    relation := to_regclass('pets');

    -- **`date` 이고 nullable 이다.** NULL 이 "아직 함께 있는 아이"라는 뜻이라,
    -- NOT NULL 이 걸리면 살아 있는 아이에게 배웅한 날을 적어야 한다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'farewell_on'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'date'
          AND NOT a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: pets.farewell_on (want date, nullable)';
    END IF;

    -- **기본값이 없어야 한다** — `pets.registered`(#288) · `app_users.room_name`(#292)과
    -- 같은 종류의 단언이다. 기본값이 걸리면 전 강아지가 배웅된 것이 된다.
    SELECT pg_get_expr(d.adbin, d.adrelid) INTO default_expression
    FROM pg_attrdef d
    JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE d.adrelid = relation AND a.attname = 'farewell_on';
    IF default_expression IS NOT NULL THEN
        RAISE EXCEPTION 'column mismatch: pets.farewell_on must have no default, got %',
            default_expression;
    END IF;

    -- CHECK 둘. 오타 한 자로 2033년이 적히면 *"아직 안 온 날에 배웅했다"* 가 되고,
    -- 태어나기 전에 배웅한 아이도 생긴다.
    --
    -- ⚠ `pets_farewell_not_future` 는 `CURRENT_DATE` 를 써서 **IMMUTABLE 이 아니다.**
    -- Postgres 가 막지는 않지만 이미 들어간 행을 다시 검사하지 않으므로 **"넣을 때만"
    -- 보는 규칙**이다. 그래도 오타를 거르는 데는 충분하다 — 마이그레이션 주석의 판단이고
    -- 여기서는 그 제약이 살아 있는지만 본다.
    FOR item IN SELECT * FROM (VALUES
        ('pets_farewell_not_future'), ('pets_farewell_after_birth')
    ) AS expected(conname) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = item.conname
              AND c.contype = 'c' AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
    END LOOP;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 배웅한 아이 수. 적용 직후에는 0이고, 그 뒤로도 **행은 지워지지 않는다.**
SELECT count(*) AS pets_total,
       count(farewell_on) AS farewelled,
       count(*) FILTER (WHERE farewell_on > CURRENT_DATE) AS in_the_future_should_be_0
FROM pets;
