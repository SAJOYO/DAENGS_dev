-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
--
-- ⚠ **이 파일은 모양이 아니라 값을 잰다.** 다른 verify 들은 컬럼·제약이 섰는지 보는데,
--    이 마이그레이션이 한 일은 `documents.metadata` 에 `org` 을 채운 것이다. 그리고
--    **그 값이 실제로 지워진 적이 있다** — #268 의 `rag load` 가 `org` 없는 청크로 덮어써
--    2,592행이 통째로 사라졌고, 적재는 성공하고 예외도 안 났다 (RAG-066 ①).
--    그 사고를 잡는 자리가 여기다.
DO $verify$
DECLARE
    filled bigint;
    definition text;
BEGIN
    IF to_regclass('documents') IS NULL THEN
        RAISE EXCEPTION 'missing table: documents';
    END IF;

    -- ① 인덱스. 지역 필터가 `metadata->>'org'` 로 거르므로(RAG-063) 표현식 인덱스여야 한다.
    --    ⚠ 정의 문자열을 그대로 비교하지 않는다 — Postgres 가 다시 써서 내놓는다.
    SELECT pg_get_indexdef(i.indexrelid) INTO definition
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    WHERE i.indrelid = to_regclass('documents') AND c.relname = 'idx_documents_org'
      AND i.indisvalid AND i.indisready
      AND i.indexprs IS NOT NULL;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'index mismatch: idx_documents_org missing, invalid, '
                        'or not an expression index';
    END IF;
    IF position('org' IN definition) = 0 THEN
        RAISE EXCEPTION 'index mismatch: idx_documents_org is not on metadata->>org, got %',
            definition;
    END IF;

    -- ② **값이 실제로 채워졌는가.** 이것이 이 파일의 이유다 (머리말 ⚠).
    --    조례·보조금 문서에는 `org` 이 있어야 한다 — 그 두 소스가 지자체 문서다.
    SELECT count(*) INTO filled
    FROM documents
    WHERE metadata->>'source_id' IN ('ordinance-search', 'benefit24-services')
      AND metadata->>'org' IS NULL;
    IF filled > 0 THEN
        RAISE EXCEPTION 'row mismatch: % local-government documents have no org', filled;
    END IF;

    -- ③ 빈 문자열로 채워지지 않았는가. `''` 는 있는 것처럼 보이면서 필터를 통과시킨다.
    SELECT count(*) INTO filled
    FROM documents WHERE metadata ? 'org' AND btrim(metadata->>'org') = '';
    IF filled > 0 THEN
        RAISE EXCEPTION 'row mismatch: % documents have a blank org', filled;
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- `org` 을 가진 행 수와 고유 지자체 수. 2026-09-06 실측: 2,592행 · 146곳.
SELECT count(*) FILTER (WHERE metadata ? 'org') AS with_org,
       count(DISTINCT metadata->>'org') AS distinct_orgs,
       count(*) AS documents_total
FROM documents;

-- 소스별로 몇 건이 채워졌나. ordinance-search 와 benefit24-services 만 나와야 한다.
SELECT metadata->>'source_id' AS source_id, count(*) AS rows
FROM documents WHERE metadata ? 'org'
GROUP BY 1 ORDER BY 2 DESC;
