-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('pets') IS NULL THEN
        RAISE EXCEPTION 'missing table: pets';
    END IF;
    relation := to_regclass('pets');

    -- ① 칸 여덟. **전부 NULL 허용이어야 한다** — 사진이 없는 강아지가 정상이고,
    --    NOT NULL 을 걸면 기존 행이 전부 위반이 된다.
    FOR item IN SELECT * FROM (VALUES
        ('photo_storage_key', 'character varying(200)'),
        ('photo_content_type', 'character varying(40)'),
        ('photo_generation', 'character varying(64)'),
        ('photo_size_bytes', 'integer'),
        ('photo_updated_at', 'timestamp with time zone'),
        ('photo_pending_key', 'character varying(200)'),
        ('photo_pending_content_type', 'character varying(40)'),
        ('photo_pending_at', 'timestamp with time zone')
    ) AS expected(column_name, type_name) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND NOT a.attnotnull
        ) THEN
            RAISE EXCEPTION 'column mismatch: pets.% (type %, nullable)',
                item.column_name, item.type_name;
        END IF;
    END LOOP;

    -- ② 제약 다섯. **함께 채워지는 칸들이 함께 채워지는가**를 DB 가 지키는 자리다 —
    --    앱이 중간에 죽어 storage_key 만 있고 content_type 이 없는 행이 생기면
    --    앱은 사진이 있다고 믿고 열려다 실패한다.
    FOR item IN SELECT * FROM (VALUES
        ('pets_photo_set'), ('pets_photo_pending_set'),
        ('pets_photo_content_type'), ('pets_photo_pending_content_type'),
        ('pets_photo_size')
    ) AS expected(conname) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = item.conname
              AND c.contype = 'c' AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
    END LOOP;

    -- ③ 부분 UNIQUE 인덱스 둘. **`WHERE … IS NOT NULL` 이 빠지면** 사진 없는 강아지가
    --    둘 이상일 수 없게 된다 — NULL 이 유일해야 하는 값이 되어 두 번째 등록이 막힌다.
    FOR item IN SELECT * FROM (VALUES
        ('idx_pets_photo_pending_key', 'photo_pending_key'),
        ('idx_pets_photo_storage_key', 'photo_storage_key')
    ) AS expected(index_name, column_name) LOOP
        SELECT pg_get_indexdef(i.indexrelid) INTO definition
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = relation AND c.relname = item.index_name
          AND i.indisvalid AND i.indisready AND i.indisunique;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'index mismatch: % missing, invalid, or not unique', item.index_name;
        END IF;
        IF position(item.column_name || ' IS NOT NULL' IN definition) = 0 THEN
            RAISE EXCEPTION 'index mismatch: % is not partial on % IS NOT NULL, got %',
                item.index_name, item.column_name, definition;
        END IF;
    END LOOP;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 기존 강아지는 전부 사진 없음이어야 한다 — 이 마이그레이션은 값을 안 채운다.
SELECT count(*) AS pets_total,
       count(photo_storage_key) AS with_photo,
       count(photo_pending_key) AS with_pending
FROM pets;

-- 반쪽으로 채워진 행이 있나. **0행이 나와야 한다** (제약이 막지만 눈으로도 본다).
SELECT count(*) AS half_filled
FROM pets
WHERE (photo_storage_key IS NULL) <> (photo_content_type IS NULL)
   OR (photo_pending_key IS NULL) <> (photo_pending_at IS NULL);
