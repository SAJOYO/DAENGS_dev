-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- 한 칸짜리 마이그레이션이고 단언도 셋뿐인데, **셋째(기본값 없음)가 이 파일의 이유다** —
-- `pets.registered`(#288)와 같은 종류다. NULL 이 "아직 안 정했다"이고 그때 앱이 대표
-- 강아지 이름으로 짓는다. 기본값이 걸리면 그 "아직"이 사라진다.
DO $verify$
DECLARE
    relation regclass;
    default_expression text;
BEGIN
    IF to_regclass('app_users') IS NULL THEN
        RAISE EXCEPTION 'missing table: app_users';
    END IF;
    relation := to_regclass('app_users');

    -- ① 칸이 있고 ② **varchar(20) · nullable** 이어야 한다.
    --    길이를 넓히면 앱의 입력 제한과 갈라지고, NOT NULL 이 걸리면 기존 회원 전체가
    --    막힌다 (전부 NULL 로 들어와 있다 — 마이그레이션이 백필을 안 한다).
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'room_name'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'character varying(20)'
          AND NOT a.attnotnull
    ) THEN
        RAISE EXCEPTION
            'column mismatch: app_users.room_name (want character varying(20), nullable)';
    END IF;

    -- ③ **기본값이 없어야 한다.**
    --
    -- `DEFAULT '네옹이네'` 같은 것이 걸리면 타입도 널 허용도 그대로라 ② 는 통과한다.
    -- 그런데 그것이 바로 이 마이그레이션이 고치러 온 버그다 — 방 앞 이름표가 앱에
    -- 박혀 있어서 **누가 쓰든 남의 강아지 이름이 자기 방에 걸려 있었다.**
    SELECT pg_get_expr(d.adbin, d.adrelid) INTO default_expression
    FROM pg_attrdef d
    JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE d.adrelid = relation AND a.attname = 'room_name';
    IF default_expression IS NOT NULL THEN
        RAISE EXCEPTION 'column mismatch: app_users.room_name must have no default, got %',
            default_expression;
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 이름표를 정한 회원 수. 적용 직후에는 0이고, 그때 모두가 예전과 같은 화면을 본다.
SELECT count(*) AS users_total,
       count(room_name) AS named,
       count(*) - count(room_name) AS still_default
FROM app_users;
