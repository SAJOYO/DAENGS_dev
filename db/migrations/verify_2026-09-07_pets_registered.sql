-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다. `db-migrate.yml` 도 종료 코드로 성공을 판정하므로
-- (`psql … < verify_%MIG_FILE% || exit 1`) SELECT 만 있으면 **틀려도 통과한다.**
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
DO $verify$
DECLARE
    relation regclass;
    default_expression text;
BEGIN
    IF to_regclass('pets') IS NULL THEN
        RAISE EXCEPTION 'missing table: pets';
    END IF;
    relation := to_regclass('pets');

    -- ① 칸이 생겼나. **boolean 이고 NULL 을 허용해야 한다** — NULL 이 '모름'이다.
    --    NOT NULL 이 걸리면 모름을 적을 자리가 사라지고, 앱은 안 물어본 강아지에게도
    --    true/false 중 하나를 골라 넣어야 한다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'registered'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'boolean'
          AND NOT a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: pets.registered (want boolean, nullable)';
    END IF;

    -- ② **기본값이 없어야 한다.** 이 단언이 이 파일의 값이다.
    --
    -- `DEFAULT false` 는 스키마를 안 깨고 조용히 뜻만 바꾼다 — 타입도 널 허용도 그대로라
    -- ① 은 통과한다. 그런데 그 순간 "안 물어봤다"가 전부 "안 했다"가 되고, F5 가 이미
    -- 등록한 사람에게 등록하라고 보낸다. 옆 칸 `neutered` 가 지키는 규칙과 같다.
    SELECT pg_get_expr(d.adbin, d.adrelid) INTO default_expression
    FROM pg_attrdef d
    JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE d.adrelid = relation AND a.attname = 'registered';
    IF default_expression IS NOT NULL THEN
        RAISE EXCEPTION 'column mismatch: pets.registered must have no default, got %',
            default_expression;
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 기존 행은 **전부 NULL 이어야 한다** — 백필할 정답이 없어서 안 채웠다.
-- 앱 입력 화면이 생기면 그때부터 사람이 답한 값만 들어온다.
SELECT count(*) AS pets_total,
       count(registered) AS answered,
       count(*) - count(registered) AS unknown
FROM pets;
