-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다. `db-migrate.yml` 도 종료 코드로 성공을 판정하므로
-- (`psql … < verify_%MIG_FILE% || exit 1`) SELECT 만 있으면 **틀려도 통과한다.**
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('app_users') IS NULL THEN
        RAISE EXCEPTION 'missing table: app_users';
    END IF;
    relation := to_regclass('app_users');

    -- ① 칸이 생겼나. **NULL 을 허용해야 한다** — NULL 이 "아직 발급 전"이다.
    FOR item IN SELECT * FROM (VALUES
        ('nickname', 'character varying(30)', 'false')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: app_users.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ② 인덱스가 **표현식(lower)** 인가. 그냥 컬럼 UNIQUE 면 'Neo' 와 'neo' 가 둘 다 생긴다.
    --    이름만 보면 안 된다 — 같은 이름으로 다른 인덱스를 만들 수 있다.
    --
    -- ⚠ **정의 문자열을 그대로 비교하지 않는다.** Postgres 가 `lower((nickname)::text)` 로
    --    다시 써서 내놓기 때문에 `lower(nickname` 같은 조각은 안 맞는다 (2026-09-06 CI 실측).
    --    그래서 **표현식 인덱스인가**(`indexprs`)를 카탈로그로 보고, 함수 이름만 문자열로 본다.
    SELECT pg_get_indexdef(i.indexrelid) INTO definition
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    WHERE i.indrelid = relation AND c.relname = 'idx_app_users_nickname'
      AND i.indisvalid AND i.indisready AND i.indisunique
      AND i.indexprs IS NOT NULL;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'index mismatch: idx_app_users_nickname missing, invalid, '
                        'not unique, or not an expression index';
    END IF;
    IF position('lower(' IN definition) = 0 OR position('nickname' IN definition) = 0 THEN
        RAISE EXCEPTION 'index mismatch: idx_app_users_nickname is not on lower(nickname), got %',
            definition;
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 기존 회원은 전부 NULL 이어야 한다 — 여기서 채우지 않는다.
-- 다음 로그인에 서버가 발급한다 (services/app_auth.py 의 _ensure_nickname).
SELECT count(*) AS users_total,
       count(nickname) AS with_nickname,
       count(*) - count(nickname) AS pending
FROM app_users;

-- 중복이 없나. **0행이 나와야 한다.**
SELECT lower(nickname) AS folded, count(*) AS n
FROM app_users
WHERE nickname IS NOT NULL
GROUP BY lower(nickname)
HAVING count(*) > 1;

-- 대소문자만 다른 이름이 막히는지. **이 줄은 실패해야 맞다** (에러가 나면 통과다).
-- BEGIN;
-- INSERT INTO app_users (kakao_id, nickname) VALUES (-9003, 'Neo'), (-9004, 'neo');
-- ROLLBACK;
