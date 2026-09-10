-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다 (CHECKS 의 `ocr_consent` 항목).
--
-- ⚠ 실패 문구에 `mismatch` 나 `missing table` 이 들어가야 한다 — 하네스가 그 낱말로
--   "verifier 가 잡았다" 와 "변조 SQL 이 죽었다" 를 가른다.
--
-- **제일 중요한 단언은 ③ 이다 — 이 두 칸에 기본값이 없어야 한다.** 나머지는 스키마가
-- 틀리면 코드가 시끄럽게 죽지만, DEFAULT NOW() 한 줄은 아무것도 안 깨뜨리면서 **아무도
-- 누른 적 없는 동의를 전 회원에게 만들어 낸다.** 그 상태로 학습셋을 뽑으면 근거 없이
-- 모은 데이터가 되고, 그때는 이미 되돌릴 수 없다. 조용히 틀리는 유일한 칸이라 여기서 잰다.
DO $verify$
DECLARE
    item record;
    relation regclass;
BEGIN
    IF to_regclass('app_users') IS NULL THEN
        RAISE EXCEPTION 'missing table: app_users';
    END IF;
    relation := to_regclass('app_users');

    -- ① 칸 둘. **둘 다 nullable 이어야 한다** — NOT NULL 이면 미동의를 표현할 수 없다.
    FOR item IN SELECT * FROM (VALUES
        ('ocr_consent_at',      'timestamp with time zone'),
        ('ocr_consent_version', 'character varying(20)')
    ) AS expected(column_name, type_name) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND NOT a.attnotnull
        ) THEN
            RAISE EXCEPTION 'column mismatch: app_users.% (type %, nullable)',
                item.column_name, item.type_name;
        END IF;
    END LOOP;

    -- ② 짝 CHECK. 시각만 있고 판이 없으면 "무엇에 동의했는지" 를 못 말한다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = 'app_users_ocr_consent_pair'
          AND c.contype = 'c'
    ) THEN
        RAISE EXCEPTION
            'constraint mismatch on app_users: app_users_ocr_consent_pair';
    END IF;

    -- ③ **기본값이 없어야 한다.** 이 파일의 존재 이유다 (머리말).
    FOR item IN SELECT * FROM (VALUES
        ('ocr_consent_at'), ('ocr_consent_version')
    ) AS expected(column_name) LOOP
        IF EXISTS (
            SELECT 1 FROM pg_attrdef d
            JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE d.adrelid = relation AND a.attname = item.column_name
        ) THEN
            RAISE EXCEPTION
                'column mismatch: app_users.% 에 기본값이 있으면 안 된다 (아무도 안 누른 동의가 생긴다)',
                item.column_name;
        END IF;
    END LOOP;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언이 다 통과한 뒤에만 여기 온다).
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type,
       a.attnotnull, a.atthasdef
FROM pg_attribute a
WHERE a.attrelid = to_regclass('app_users') AND a.attnum > 0 AND NOT a.attisdropped
  AND a.attname LIKE 'ocr_consent%'
ORDER BY a.attnum;
