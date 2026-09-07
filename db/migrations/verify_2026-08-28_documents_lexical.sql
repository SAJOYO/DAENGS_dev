-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- 이 장이 지키는 것은 **렉시컬 축이 조용히 죽지 않는 것**이다. 하이브리드 검색은
-- dense + `ts_rank` 두 축인데(RAG-035), 이 컬럼들이 망가져도 **dense 로는 계속 찾히므로
-- 예외도 로그도 안 난다.** 검색 품질만 반쯤 내려앉는다.
DO $verify$
DECLARE
    relation regclass;
    default_expression text;
    generation_expression text;
    empty_tokens bigint;
    loaded_rows bigint;
BEGIN
    IF to_regclass('documents') IS NULL THEN
        RAISE EXCEPTION 'missing table: documents';
    END IF;
    relation := to_regclass('documents');

    -- ① 토큰 칸. `rag load` 가 Kiwi 형태소 결과를 넣는다. **NOT NULL 이 존재 이유다** —
    --    안 채운 문서는 렉시컬 반쪽에서 사라지는데 아무 예외도 안 난다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'content_tokens'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'text'
          AND a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: documents.content_tokens (want text, not null)';
    END IF;

    -- ② **적재가 끝난 DB 라면 기본값이 없어야 한다 — 2단계가 돌았다는 증거다.**
    --
    -- 마이그레이션은 **두 단계**다. 1단계(파일 본문)가 `DEFAULT ''` 로 컬럼을 붙이고
    -- — 기존 행이 비어 있어 NOT NULL 을 바로 못 걸기 때문이다 — 적재로 토큰을 채운
    -- **뒤에** 2단계(파일 아래 주석)가 기본값을 뗀다. 기본값이 남으면 **안 채운 INSERT 가
    -- 조용히 성공하고**, 그 행은 dense 로만 찾히는 반쪽 문서가 된다.
    --
    -- ⚠️ **그래서 조건부다.** 이 파일을 막 적용한 직후(= 1단계만 끝난 상태)에는 기본값이
    -- 있는 것이 정상이고, 그때 예외를 내면 `db-migrate.yml` 이 **정상 적용을 실패로 만든다.**
    -- 판정 기준은 "토큰이 찼는가" 하나다 — 마이그레이션 본문이 2단계의 조건으로 적어 둔 것과
    -- 같은 기준이고(`SELECT count(*) ... WHERE content_tokens = ''` 가 0), 그 상태에서
    -- 기본값이 남아 있으면 그것은 **2단계를 빠뜨린 것**이다.
    -- 2026-09-07 실측: 집 서버·GCP 둘 다 기본값 없음 · 빈 토큰 0행 (2단계 적용 완료).
    SELECT pg_get_expr(d.adbin, d.adrelid) INTO default_expression
    FROM pg_attrdef d
    JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE d.adrelid = relation AND a.attname = 'content_tokens';
    IF default_expression IS NOT NULL THEN
        EXECUTE format('SELECT count(*) FROM %s WHERE content_tokens = %L', relation, '')
            INTO empty_tokens;
        EXECUTE format('SELECT count(*) FROM %s', relation) INTO loaded_rows;
        IF loaded_rows > 0 AND empty_tokens = 0 THEN
            RAISE EXCEPTION 'column mismatch: documents.content_tokens must have no default '
                            '(토큰은 다 찼는데 2단계 DROP DEFAULT 를 안 돌렸다), got %',
                default_expression;
        END IF;
        RAISE NOTICE '  1단계까지만 적용된 상태다 (빈 토큰 % / % 행). '
                     '적재 뒤 2단계(ALTER ... DROP DEFAULT)를 돌릴 것.',
            empty_tokens, loaded_rows;
    END IF;

    -- ③ tsvector 는 **생성 컬럼이어야 한다.** 보통 컬럼으로 바뀌면 토큰과 어긋날 수 있는데
    --    타입이 같아서(tsvector) 모양만 보는 검사는 통과한다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'content_tsv'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'tsvector'
          AND a.attgenerated = 's'
    ) THEN
        RAISE EXCEPTION
            'column mismatch: documents.content_tsv (want tsvector, GENERATED ALWAYS ... STORED)';
    END IF;

    -- ④ **생성식이 `simple` 이어야 한다 — 이 파일에서 가장 잡기 어려운 변조다.**
    --
    -- PG 에 한국어 config 가 없다. 형태소 분리는 애플리케이션이 끝내고 오므로 여기서는
    -- 공백으로만 자르면 된다. `english` 로 두면 한국어에 영어 스테머가 걸려 토큰이
    -- 망가지는데 — **타입도 생성 여부도 그대로라 ③ 은 통과하고**, 검색은 계속 결과를
    -- 돌려주며(dense 가 있다), 예외도 경고도 없다.
    -- 생성 컬럼의 식도 `pg_attrdef` 에 들어간다 (기본값과 같은 자리다).
    SELECT pg_get_expr(d.adbin, d.adrelid) INTO generation_expression
    FROM pg_attrdef d
    JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE d.adrelid = relation AND a.attname = 'content_tsv';
    IF generation_expression IS NULL THEN
        RAISE EXCEPTION 'column mismatch: documents.content_tsv has no generation expression';
    END IF;
    -- 정의 문자열을 통째로 비교하지 않는다 (README 의 함정 ①) — Postgres 가
    -- `'simple'::regconfig` 로 다시 써서 내놓는다. config 이름과 입력 칸만 본다.
    IF position('simple' IN generation_expression) = 0
       OR position('content_tokens' IN generation_expression) = 0 THEN
        RAISE EXCEPTION
            'column mismatch: content_tsv must be to_tsvector(''simple'', content_tokens), got %',
            generation_expression;
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 토큰이 빈 문서. **0이어야 한다** — 1 이상이면 그 문서는 dense 로만 찾힌다.
-- (적용 직후 · 적재 전에는 전 행이 비어 있는 것이 정상이다.)
SELECT count(*) AS documents_total,
       count(*) FILTER (WHERE content_tokens = '') AS empty_tokens,
       count(*) FILTER (WHERE content_tsv IS NULL) AS null_tsv
FROM documents;

-- GIN 인덱스는 여기서 만들지 않는다 (`db/indexes.sql` 이 "적재 뒤 수동"을 못 박았다).
-- 있는지는 보여 준다 — 없으면 렉시컬 축이 전수 스캔이다.
SELECT c.relname AS lexical_index
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
WHERE i.indrelid = to_regclass('documents') AND c.relname = 'idx_documents_tsv';
