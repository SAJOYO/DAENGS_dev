-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다 (CHECKS 의 `pet_members` 항목). SELECT 만 있으면 틀려도
-- 통과하므로 사람이 볼 질의는 단언 뒤에 둔다.
--
-- ⚠ 실패 문구에 `mismatch` 나 `missing table` 이 들어가야 한다 — 하네스가 그 낱말로
--   "verifier 가 잡았다" 와 "변조 SQL 이 죽었다" 를 가른다.
--
-- 제일 중요한 단언은 **`idx_pet_members_app_user`** 다 — PK 가 (pet_id, …) 라 이 인덱스
-- 없이는 "내가 돌보는 강아지 전부" 조회(앱을 켤 때마다)가 순차 스캔이 된다. 트리거 둘도
-- 여기서 본다 — 탈퇴 정리와 대표-겸-돌보미 금지가 DB 쪽 정합성의 전부다 (docs/co-care.md).
DO $verify$
DECLARE
    item record;
    relation regclass;
BEGIN
    -- ⓪ 표 존재.
    IF to_regclass('pet_members') IS NULL THEN
        RAISE EXCEPTION 'missing table: pet_members';
    END IF;
    IF to_regclass('pet_invites') IS NULL THEN
        RAISE EXCEPTION 'missing table: pet_invites';
    END IF;

    -- ① pet_members 열.
    relation := to_regclass('pet_members');
    FOR item IN SELECT * FROM (VALUES
        ('pet_id',      'uuid',                     'true'),
        ('app_user_id', 'uuid',                     'true'),
        ('joined_at',   'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: pet_members.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ② pet_members 의 복합 PK · FK 둘(둘 다 CASCADE). 열 순서까지 본다.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'pet_id,app_user_id', NULL,         NULL, NULL),
        ('f', 'pet_id',             'pets',       'id', 'c'),
        ('f', 'app_user_id',        'app_users',  'id', 'c')
    ) AS expected(kind, columns, target_table, target_columns, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND (item.kind <> 'f' OR (
                  c.confrelid = to_regclass(item.target_table)
                  AND c.confdeltype::text = item.delete_action
                  AND ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num, pos)
                            JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num
                            ORDER BY k.pos) = string_to_array(item.target_columns, ',')
              ))
              AND (item.kind <> 'p' OR EXISTS (
                  SELECT 1 FROM pg_index i WHERE i.indexrelid = c.conindid
                    AND i.indisvalid AND i.indisready AND i.indisunique
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: pet_members kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- ③ 돌보미 조회 인덱스. 없으면 앱을 켤 때마다 순차 스캔이 된다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_class i
        JOIN pg_index x ON x.indexrelid = i.oid
        WHERE x.indrelid = relation AND i.relname = 'idx_pet_members_app_user'
    ) THEN
        RAISE EXCEPTION 'index mismatch on pet_members: idx_pet_members_app_user';
    END IF;

    -- ④ pet_invites 열.
    relation := to_regclass('pet_invites');
    FOR item IN SELECT * FROM (VALUES
        ('id',         'uuid',                     'true'),
        ('pet_id',     'uuid',                     'true'),
        ('invited_by', 'uuid',                     'true'),
        ('token_hash', 'character(64)',            'true'),
        ('expires_at', 'timestamp with time zone', 'true'),
        ('created_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: pet_invites.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ⑤ pet_invites 의 PK · FK 둘(둘 다 CASCADE) · token_hash UNIQUE.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id',         NULL,        NULL, NULL),
        ('f', 'pet_id',     'pets',      'id', 'c'),
        ('f', 'invited_by', 'app_users', 'id', 'c'),
        ('u', 'token_hash', NULL,        NULL, NULL)
    ) AS expected(kind, columns, target_table, target_columns, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND (item.kind <> 'f' OR (
                  c.confrelid = to_regclass(item.target_table)
                  AND c.confdeltype::text = item.delete_action
                  AND ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num, pos)
                            JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num
                            ORDER BY k.pos) = string_to_array(item.target_columns, ',')
              ))
              AND (item.kind NOT IN ('p', 'u') OR EXISTS (
                  SELECT 1 FROM pg_index i WHERE i.indexrelid = c.conindid
                    AND i.indisvalid AND i.indisready AND i.indisunique
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: pet_invites kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- ⑥ 초대 표 조회 인덱스.
    IF NOT EXISTS (
        SELECT 1 FROM pg_class i
        JOIN pg_index x ON x.indexrelid = i.oid
        WHERE x.indrelid = relation AND i.relname = 'idx_pet_invites_pet'
    ) THEN
        RAISE EXCEPTION 'index mismatch on pet_invites: idx_pet_invites_pet';
    END IF;

    -- ⑦ 트리거 둘. app_users 는 탈퇴해도 안 지워지므로 FK 로는 절대 정리가 안 돈다 —
    --   이 둘이 유일한 방어선이다 (docs/co-care.md "이 문서 전체를 관통하는 함정").
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'app_users'::regclass
          AND tgname = 'pet_membership_owner_cleanup' AND tgenabled = 'O'
    ) THEN
        RAISE EXCEPTION 'trigger mismatch: app_users.pet_membership_owner_cleanup missing or disabled';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'pet_members'::regclass
          AND tgname = 'pet_members_not_owner' AND tgenabled = 'O'
    ) THEN
        RAISE EXCEPTION 'trigger mismatch: pet_members.pet_members_not_owner missing or disabled';
    END IF;

    -- ⑧ care_events.actor_app_user_id — 개명이 안 됐으면 다음 사람이 옛 이름을 '소유자'로
    --   읽고 권한 검사를 잘못 짠다 (models/care_event.py 의 옛 주석이 그 함정이다).
    relation := to_regclass('care_events');
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'actor_app_user_id'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'uuid'
          AND a.attnotnull = false
    ) THEN
        RAISE EXCEPTION 'column mismatch: care_events.actor_app_user_id (type uuid, not null false)';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.contype = 'f' AND c.conname = 'care_events_actor_fkey'
          AND c.convalidated
          AND c.confrelid = to_regclass('app_users')
          AND c.confdeltype = 'n'  -- SET NULL: 챙긴 사람이 떠나도 "그날 먹었다"는 남는다.
          AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['actor_app_user_id']
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: care_events_actor_fkey';
    END IF;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언이 다 통과한 뒤에만 여기 온다).
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull
FROM pg_attribute a
WHERE a.attrelid = to_regclass('pet_members') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull
FROM pg_attribute a
WHERE a.attrelid = to_regclass('pet_invites') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
