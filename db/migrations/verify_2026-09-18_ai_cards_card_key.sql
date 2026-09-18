-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다. 실패 문구에는 하네스 어휘(`... mismatch` / `missing table`)를 쓴다.
--
-- 이 장(#593, D-085)이 세우는 것은 넷이다.
--   ⓐ `card_key` varchar(20) NOT NULL — 무엇을 만들었나(달이면 "4", 종류면 "strawberry")
--   ⓑ `month` 는 **nullable** — 종류 카드에는 달이 없다. NOT NULL 로 되돌아가면 종류 카드를
--      아예 못 만든다(옛 모양이라 에러도 「없는 칸」이 아니라 「NULL 못 넣음」으로 뜬다)
--   ⓒ `ai_cards_month` 가 **card_key 를 보는** 새 정의 — 옛 정의(`month BETWEEN 1 AND 12`)는
--      이름이 같고 살아 있는 채로 종류 카드의 month=NULL 을 그냥 통과시키므로, 이름만 보는
--      검사로는 되돌아간 것을 못 잡는다. 정의 안에 `card_key` 가 있는지까지 본다
--   ⓓ 백필 — 옛 행의 `card_key` 가 자기 달의 문자열이어야 한다. 모양만 봐서는 "칸은 멀쩡한데
--      값이 엉뚱한" 상태를 못 잡는다(`documents_org_backfill` 에서 실제로 난 사고)
--
-- 그리고 이 장이 **건드리지 않아야 하는 것** 셋을 같이 본다 — 표를 다시 만드는 식으로 고치면
-- 조용히 사라지는 자리다(`idx_ai_cards_one_generating`·`idx_ai_cards_storage_key`·`ai_cards_ready_set`).
-- 그 셋의 정의 자체는 각자의 verify(2026-09-14 · 2026-09-16)가 본다 — 여기서는 생존만 본다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
    stray bigint;
BEGIN
    IF to_regclass('ai_cards') IS NULL THEN
        RAISE EXCEPTION 'missing table: ai_cards';
    END IF;
    relation := to_regclass('ai_cards');

    -- ⓐ·ⓑ 칸 둘. required 는 NOT NULL 여부다 — month 는 **false** 여야 한다.
    FOR item IN SELECT * FROM (VALUES
        ('card_key', 'character varying(20)', 'true'),
        ('month', 'smallint', 'false')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: ai_cards.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ⓒ 새 CHECK 둘. NOT VALID 는 "있어도 없는 것" 이라 convalidated 를 본다.
    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = relation AND c.conname = 'ai_cards_month'
      AND c.contype = 'c' AND c.convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'constraint mismatch: ai_cards_month missing or not validated';
    END IF;
    -- 옛 정의에는 card_key 가 없다. 이름만 같은 채로 되돌아간 것을 여기서 가른다.
    IF position('card_key' IN definition) = 0 THEN
        RAISE EXCEPTION 'constraint mismatch: ai_cards_month lost its card_key pairing, got %',
            definition;
    END IF;
    -- 종류 카드 갈래(달이 비는 쪽)가 통째로 빠진 정의를 가른다.
    IF position('month IS NULL' IN definition) = 0 THEN
        RAISE EXCEPTION 'constraint mismatch: ai_cards_month lost its kind-card branch'
            ' (month IS NULL), got %', definition;
    END IF;

    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = relation AND c.conname = 'ai_cards_card_key'
      AND c.contype = 'c' AND c.convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'constraint mismatch: ai_cards_card_key missing or not validated';
    END IF;
    IF position('btrim' IN definition) = 0 THEN
        RAISE EXCEPTION 'constraint mismatch: ai_cards_card_key lost its blank guard, got %',
            definition;
    END IF;

    -- ⓓ 백필. 달이 있는 행의 card_key 는 그 달의 문자열이다. CHECK 가 같은 것을 막지만,
    -- 그 CHECK 가 NOT VALID 로 붙으면 값은 얼마든지 어긋난 채 남는다 — 값을 직접 센다.
    SELECT count(*) INTO stray FROM ai_cards WHERE month IS NOT NULL AND card_key <> month::text;
    IF stray > 0 THEN
        RAISE EXCEPTION 'backfill mismatch: ai_cards % rows have month but a different card_key',
            stray;
    END IF;
    SELECT count(*) INTO stray FROM ai_cards WHERE card_key IS NULL OR btrim(card_key) = '';
    IF stray > 0 THEN
        RAISE EXCEPTION 'backfill mismatch: ai_cards % rows have an empty card_key', stray;
    END IF;

    -- 건드리지 않아야 하는 셋. 표를 다시 만드는 식으로 고치면 여기서 걸린다.
    FOR item IN SELECT * FROM (VALUES
        ('idx_ai_cards_one_generating', 'true'),
        ('idx_ai_cards_storage_key', 'true')
    ) AS expected(index_name, unique_wanted) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            WHERE i.indrelid = relation AND c.relname = item.index_name
              AND i.indisvalid AND i.indisready
              AND i.indisunique = item.unique_wanted::boolean
        ) THEN
            RAISE EXCEPTION 'index mismatch: % missing, invalid, or unique flag is not %',
                item.index_name, item.unique_wanted;
        END IF;
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = 'ai_cards_ready_set'
          AND c.contype = 'c' AND c.convalidated
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: ai_cards_ready_set missing or not validated';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- 적용 직후에는 달 카드만 있고 month 가 빈 줄이 하나도 없는 것이 정상이다.
-- ─────────────────────────────────────────────────────────────────────────
SELECT card_key, count(*) AS rows, count(month) AS with_month
FROM ai_cards GROUP BY card_key ORDER BY card_key;
