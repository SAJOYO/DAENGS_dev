-- verify_2026-09-05_documents_org_backfill.sql
-- 적용 후 눈으로 보는 질의 모음. **고치는 것은 없다.**
-- 짝: 2026-09-05_documents_org_backfill.sql (RAG-063 / #262)

-- 1) 몇 청크가 채워졌나. 2026-09-05 집 서버 기준 **2,592** 다
--    (문서 245개 = 조례 208 + 보조금24 37 이 청크로 펼쳐진 수).
SELECT count(*) AS org_chunks
FROM documents
WHERE metadata->>'org' IS NOT NULL;

-- 2) 고유 지자체 수. **146** 이어야 한다. 이 값이 곧 `search.known_orgs()` 의 사전이고,
--    비면 지역 필터가 조용히 안 켜진다 — 검색은 계속 되므로 예외가 안 난다.
SELECT count(DISTINCT metadata->>'org') AS orgs
FROM documents
WHERE metadata->>'org' IS NOT NULL;

-- 3) 소스별 분포. `ordinance-search` 와 `benefit24-services` 두 곳에만 있어야 한다 —
--    다른 소스에 `org` 이 붙었다면 doc_id 매칭이 헐거운 것이다.
SELECT metadata->>'source_id' AS source_id, count(*) AS chunks,
       count(DISTINCT metadata->>'org') AS orgs
FROM documents
WHERE metadata->>'org' IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC;

-- 4) **값에 공백이 정상인가.** `org` 은 법제처가 정규화해서 주는 값이라 `부산광역시 동래구`
--    처럼 띄어져 있어야 한다. 조례명(`document_title`)은 `부산광역시동래구` 로 붙어 있는데,
--    그 둘을 헷갈리면 지역 필터가 `document_title` 을 파싱하는 쪽으로 잘못 간다 (RAG-063).
SELECT DISTINCT metadata->>'org' AS org, document_title
FROM documents
WHERE metadata->>'org' LIKE '부산%'
ORDER BY 1
LIMIT 10;

-- 5) 인덱스가 **표현식**으로 섰나. `metadata->>'org'` 가 indexdef 에 보여야 한다 —
--    없으면 지역 질의마다 전수 스캔이다.
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'documents' AND indexname = 'idx_documents_org';

-- 6) 여러 번 돌려도 안전한가. 이 파일을 다시 적용한 뒤 1)·2) 의 수가 그대로면 통과다.
--    (`||` 는 같은 키를 덮어쓰고, UPDATE 는 `IS DISTINCT FROM` 으로 바뀔 행만 고른다.)
