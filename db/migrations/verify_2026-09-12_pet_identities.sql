-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다 (CHECKS 의 `pet_identities` 항목). SELECT 만 있으면
-- 틀려도 통과하므로 사람이 볼 질의는 단언 뒤에 둔다.
--
-- ⚠ 실패 문구에 `mismatch` 나 `missing table` 이 들어가야 한다 — 하네스가 그 낱말로
--   "verifier 가 잡았다" 와 "변조 SQL 이 죽었다" 를 가른다.
DO $verify$
DECLARE
    item record;
    identities regclass;
    pets_rel regclass;
BEGIN
    IF to_regclass('pet_identities') IS NULL THEN
        RAISE EXCEPTION 'missing table: pet_identities';
    END IF;
    identities := to_regclass('pet_identities');

    IF to_regclass('pets') IS NULL THEN
        RAISE EXCEPTION 'missing table: pets';
    END IF;
    pets_rel := to_regclass('pets');

    -- ① pet_identities 의 칸 셋. 셋 다 NOT NULL 이다 — 앵커 없는 그룹은 뜻이 없다.
    FOR item IN SELECT * FROM (VALUES
        ('id',           'uuid',                     'true'),
        ('owner_pet_id', 'uuid',                     'true'),
        ('created_at',   'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = identities AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: pet_identities.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ② owner_pet_id 의 FK — pets(id), ON DELETE CASCADE. 앵커가 지워지면 그룹도
    --    사라지고, 남은 행은 ④ 의 SET NULL 로 독립 강아지가 된다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = identities AND c.contype = 'f'
          AND c.convalidated
          AND c.confrelid = pets_rel
          AND c.confdeltype = 'c'  -- CASCADE
          AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = identities AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['owner_pet_id']
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: pet_identities owner_pet_id FK (expected CASCADE to pets)';
    END IF;

    -- ③ 한 pet 행이 두 그룹의 앵커일 수 없다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        WHERE i.indrelid = identities AND ic.relname = 'pet_identities_owner_pet'
          AND i.indisunique AND i.indpred IS NULL
          AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = identities AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['owner_pet_id']
    ) THEN
        RAISE EXCEPTION 'index mismatch: pet_identities_owner_pet (expected total UNIQUE on owner_pet_id)';
    END IF;

    -- ④ pets.identity_id — **nullable 이어야 한다.** NULL 이 "연결 안 된 보통 강아지" 다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = pets_rel AND a.attname = 'identity_id'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'uuid'
          AND NOT a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: pets.identity_id (type uuid, not null false)';
    END IF;

    -- ⑤ identity_id 의 FK — pet_identities(id), **ON DELETE SET NULL.**
    --    CASCADE 로 바뀌면 그룹 행 하나 때문에 사람들의 강아지와 기록이 통째로 사라진다.
    --    이 한 칸이 이 파일에서 제일 중요한 단언이다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = pets_rel AND c.contype = 'f'
          AND c.convalidated
          AND c.confrelid = identities
          AND c.confdeltype = 'n'  -- SET NULL
          AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = pets_rel AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['identity_id']
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: pets.identity_id FK (expected SET NULL to pet_identities)';
    END IF;

    -- ⑥ 그룹의 pet 행을 모으는 조회축.
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        WHERE i.indrelid = pets_rel AND ic.relname = 'idx_pets_identity'
    ) THEN
        RAISE EXCEPTION 'index mismatch: idx_pets_identity (missing)';
    END IF;

    -- ⑦ **부분 UNIQUE** — 한 사람은 같은 그룹에 pet 행을 둘 이상 가질 수 없다.
    --    `indpred IS NOT NULL` 까지 보는 이유: WHERE 를 뗀 전체 UNIQUE 로 바뀌면
    --    연결 안 된 행들(identity_id NULL)까지 같은 규칙에 묶여, 이름은 그대로인데
    --    뜻이 달라진다. 반대로 UNIQUE 를 잃으면 기존 강아지 하나를 초대 강아지 두
    --    마리에 연결하는 것을 DB 가 더 이상 막지 못한다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        WHERE i.indrelid = pets_rel AND ic.relname = 'pets_identity_one_per_user'
          AND i.indisunique AND i.indpred IS NOT NULL
          AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = pets_rel AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['identity_id', 'app_user_id']
    ) THEN
        RAISE EXCEPTION 'index mismatch: pets_identity_one_per_user (expected partial UNIQUE on identity_id, app_user_id)';
    END IF;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언이 다 통과한 뒤에만 여기 온다).
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull
FROM pg_attribute a
WHERE a.attrelid = to_regclass('pet_identities') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;

SELECT ic.relname AS index_name, i.indisunique, pg_get_expr(i.indpred, i.indrelid) AS predicate
FROM pg_index i
JOIN pg_class ic ON ic.oid = i.indexrelid
WHERE i.indrelid = to_regclass('pets') AND ic.relname IN ('idx_pets_identity', 'pets_identity_one_per_user')
ORDER BY ic.relname;
