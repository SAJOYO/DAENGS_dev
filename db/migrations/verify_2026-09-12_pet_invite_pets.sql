-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다 (CHECKS 의 `pet_invite_pets` 항목).
--
-- ⚠ 실패 문구에 `mismatch` 나 `missing table` 이 들어가야 한다 — 하네스가 그 낱말로
--   "verifier 가 잡았다" 와 "변조 SQL 이 죽었다" 를 가른다.
--
-- **backfill 완전성도 여기서 본다** (⑦). 카탈로그만 보면 "표는 맞는데 기존 초대가 하나도
-- 안 옮겨진" 상태를 못 잡는데, 그러면 이미 뿌린 링크가 전부 죽는다.
DO $verify$
DECLARE
    item record;
    child regclass;
    invites regclass;
    pets_rel regclass;
    orphans bigint;
    mismatched bigint;
BEGIN
    IF to_regclass('pet_invite_pets') IS NULL THEN
        RAISE EXCEPTION 'missing table: pet_invite_pets';
    END IF;
    child := to_regclass('pet_invite_pets');

    IF to_regclass('pet_invites') IS NULL THEN
        RAISE EXCEPTION 'missing table: pet_invites';
    END IF;
    invites := to_regclass('pet_invites');
    pets_rel := to_regclass('pets');

    -- ① 자식 표의 칸 셋. `linked_pet_id` 만 nullable 이다 — 연결 없이 참여한 줄이다.
    FOR item IN SELECT * FROM (VALUES
        ('invite_id',     'uuid', 'true'),
        ('pet_id',        'uuid', 'true'),
        ('linked_pet_id', 'uuid', 'false')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = child AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: pet_invite_pets.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ② PK (invite_id, pet_id) — 같은 묶음에 같은 아이가 두 번 들어갈 수 없다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        WHERE i.indrelid = child AND i.indisprimary
          AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = child AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['invite_id', 'pet_id']
    ) THEN
        RAISE EXCEPTION 'index mismatch: pet_invite_pets primary key (expected invite_id, pet_id)';
    END IF;

    -- ③ FK 셋의 **삭제 동작**. invite_id·pet_id 는 CASCADE, linked_pet_id 는 SET NULL 이다.
    --    `linked_pet_id` 가 CASCADE 로 바뀌면 연결 대상이 지워질 때 **영수증 줄이 통째로**
    --    사라져, 재시도가 그때의 매핑을 복원하지 못한다.
    FOR item IN SELECT * FROM (VALUES
        ('invite_id',     'pet_invites', 'c'),
        ('pet_id',        'pets',        'c'),
        ('linked_pet_id', 'pets',        'n')
    ) AS expected(column_name, target, on_delete) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = child AND c.contype = 'f'
              AND c.convalidated
              AND c.confrelid = to_regclass(item.target)
              AND c.confdeltype = item.on_delete
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = child AND a.attnum = k.num
                        ORDER BY k.pos) = ARRAY[item.column_name]
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: pet_invite_pets.% FK (expected % to %)',
                item.column_name, item.on_delete, item.target;
        END IF;
    END LOOP;

    -- ④ 구 경로의 조회축.
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        WHERE i.indrelid = child AND ic.relname = 'idx_pet_invite_pets_pet'
    ) THEN
        RAISE EXCEPTION 'index mismatch: idx_pet_invite_pets_pet (missing)';
    END IF;

    -- ⑤ `pet_invites.pet_count` — NOT NULL 이어야 묶음 불변성이 성립한다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = invites AND a.attname = 'pet_count'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'smallint'
          AND a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: pet_invites.pet_count (type smallint, not null true)';
    END IF;

    -- ⑥ 빈 묶음을 DB 가 거절한다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = invites AND c.contype = 'c'
          AND c.conname = 'pet_invites_pet_count_check'
          AND c.convalidated
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: pet_invites_pet_count_check (missing or not validated)';
    END IF;

    -- ⑦ **backfill 완전성.** 기존 초대마다 자식 줄이 있어야 하고, 그 줄의 pet 은 앵커와
    --    같아야 하며, `pet_count` 가 실제 자식 수와 맞아야 한다. 하나라도 어긋나면 이미
    --    뿌린 링크가 수락에서 410 으로 죽는다 — 카탈로그만 보는 단언으로는 절대 못 잡는다.
    SELECT count(*) INTO orphans
    FROM pet_invites i
    WHERE NOT EXISTS (
        SELECT 1 FROM pet_invite_pets p
        WHERE p.invite_id = i.id AND p.pet_id = i.pet_id
    );
    IF orphans > 0 THEN
        RAISE EXCEPTION 'backfill mismatch: % pet_invites rows have no matching pet_invite_pets row', orphans;
    END IF;

    SELECT count(*) INTO mismatched
    FROM pet_invites i
    WHERE i.pet_count <> (
        SELECT count(*) FROM pet_invite_pets p WHERE p.invite_id = i.id
    );
    IF mismatched > 0 THEN
        RAISE EXCEPTION 'backfill mismatch: % pet_invites rows whose pet_count differs from their bundle size', mismatched;
    END IF;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언이 다 통과한 뒤에만 여기 온다).
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull
FROM pg_attribute a
WHERE a.attrelid = to_regclass('pet_invite_pets') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;

SELECT i.id AS invite_id, i.pet_count, count(p.pet_id) AS bundle_size
FROM pet_invites i
LEFT JOIN pet_invite_pets p ON p.invite_id = i.id
GROUP BY i.id, i.pet_count
ORDER BY i.id;
