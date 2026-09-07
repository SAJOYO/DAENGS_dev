-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
-- (2026-09-07 #295 에 단언형으로 다시 썼다. 그 전에는 SELECT 나열이라 **틀려도 녹색**이었다.)
--
-- 이 장이 하는 일은 둘이고 **둘째가 잊히기 쉽다**:
--   ⓐ `walk_pets` 다대다 표를 만든다 — 한 번에 두 마리를 데리고 나가는데 `walks.pet_id` 는
--      한 칸이라 한 아이의 기록만 남았다
--   ⓑ **값을 옮긴 뒤 `walks.pet_id` 를 DROP 한다** — 이미 걸어서 쌓인 행이 있다
DO $verify$
DECLARE
    item record;
    relation regclass;
BEGIN
    IF to_regclass('walk_pets') IS NULL THEN
        RAISE EXCEPTION 'missing table: walk_pets';
    END IF;
    relation := to_regclass('walk_pets');

    FOR item IN SELECT * FROM (VALUES
        ('walk_id', 'uuid', 'true'),
        ('pet_id', 'uuid', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: walk_pets.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- PK 가 **두 칸 다**여야 한다. 한 칸이면 같은 산책에 두 마리를 못 붙이는데,
    -- 그것이 이 표를 만든 이유 자체다.
    --
    -- FK 둘은 **CASCADE 다.** `walks.pet_id` 시절의 `SET NULL` 과 달라 보이지만 뜻은 같다 —
    -- 저쪽은 "행을 남기고 칸을 비운다"였고 여기는 이음줄 자체가 사라질 뿐, **산책 행은 남는다.**
    -- 무지개다리를 건넌 아이와의 산책이 없던 일이 되지 않는다는 성질이 그대로다.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'walk_id,pet_id', NULL, NULL),
        ('f', 'walk_id', 'walks', 'c'),
        ('f', 'pet_id', 'pets', 'c')
    ) AS expected(kind, columns, target_table, delete_action) LOOP
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
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: walk_pets kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- "이 아이가 이번 주에 얼마나 걸었나" 는 `pet_id` 로 들어온다. PK 가
    -- `(walk_id, pet_id)` 순이라 그 질의는 PK 인덱스를 못 탄다 — 그래서 따로 있다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = relation AND c.relname = 'walk_pets_pet_idx'
          AND i.indisvalid AND i.indisready
    ) THEN
        RAISE EXCEPTION 'index mismatch: walk_pets_pet_idx missing or invalid';
    END IF;

    -- ⓑ **옛 칸이 사라졌어야 한다.** 값을 옮기는 DO 블록만 돌고 DROP 을 빠뜨리면
    -- 두 곳에 진실이 생긴다 — 앱이 어느 쪽을 읽느냐에 따라 다른 답이 나오고,
    -- 그 어긋남을 알려 주는 신호가 하나도 없다.
    IF EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = to_regclass('walks') AND a.attname = 'pet_id'
          AND a.attnum > 0 AND NOT a.attisdropped
    ) THEN
        RAISE EXCEPTION 'column mismatch: walks.pet_id must be dropped '
                        '(값만 옮기고 DROP COLUMN 을 빠뜨리면 진실이 두 곳에 생긴다)';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 산책과 이음줄 수. `walks_without_pets` 는 0이 아닐 수 있다 —
-- 아이를 지운 뒤에도 산책은 남기 때문이다.
SELECT (SELECT count(*) FROM walks) AS walks,
       (SELECT count(*) FROM walk_pets) AS links,
       (SELECT count(*) FROM walks w
        WHERE NOT EXISTS (SELECT 1 FROM walk_pets p WHERE p.walk_id = w.id)) AS walks_without_pets;

-- 두 마리 이상 데리고 나간 산책. 이 표를 만든 이유가 이 수다.
SELECT count(*) AS walks_with_multiple_pets
FROM (SELECT walk_id FROM walk_pets GROUP BY walk_id HAVING count(*) > 1) AS m;
