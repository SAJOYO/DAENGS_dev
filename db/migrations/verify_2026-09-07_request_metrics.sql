-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다. `db-migrate.yml` 도 종료 코드로 성공을 판정하므로
-- (`psql … < verify_%MIG_FILE% || exit 1`) SELECT 만 있으면 **틀려도 통과한다.**
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- **이 표에서 제일 중요한 단언은 ⑤ 다** — "없어야 하는 열이 없는지". 다른 표의 verify 는
-- 있어야 할 것을 확인하는데, 여기서는 **질문 원문이나 회원 식별자가 슬쩍 들어오지
-- 않았는지**가 그만큼 중요하다 (D-037 · D-054). 열이 하나 늘어도 아무것도 안 깨지므로
-- 코드 리뷰만으로는 못 막는다.
DO $verify$
DECLARE
    relation regclass;
    missing text;
    forbidden text;
BEGIN
    IF to_regclass('request_metrics') IS NULL THEN
        RAISE EXCEPTION 'missing table: request_metrics';
    END IF;
    relation := to_regclass('request_metrics');

    -- ① 열과 타입. NOT NULL 여부까지 본다 — elapsed_ms 가 nullable 이 되면
    --    "지연을 못 쟀다" 와 "0ms" 가 안 갈린다.
    SELECT string_agg(want.col_name, ', ') INTO missing
    FROM (VALUES
        ('id',             'uuid',                        true),
        ('request_id',     'uuid',                        true),
        ('principal_kind', 'character varying(20)',       true),
        ('router_kind',    'character varying(20)',       false),
        ('capabilities',   'text[]',                      true),
        ('status',         'character varying(30)',       true),
        ('reason_code',    'character varying(60)',       false),
        ('error_category', 'character varying(60)',       false),
        ('elapsed_ms',     'integer',                     true),
        ('created_at',     'timestamp with time zone',    true)
    ) AS want(col_name, col_type, col_notnull)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = want.col_name
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = want.col_type
          AND a.attnotnull = want.col_notnull
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'column mismatch on request_metrics: %', missing;
    END IF;

    -- ② CHECK 제약 셋. principal_kind 의 오타는 아무 에러도 안 내면서 집계를
    --    두 갈래로 가른다 — 그래서 코드가 아니라 DB 가 막는다.
    SELECT string_agg(want.name, ', ') INTO missing
    FROM (VALUES
        ('request_metrics_elapsed_check'),
        ('request_metrics_principal_kind_check'),
        ('request_metrics_router_kind_check')
    ) AS want(name)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = want.name AND c.contype = 'c'
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing CHECK on request_metrics: %', missing;
    END IF;

    -- ③ 인덱스 둘. created_at 은 기간 집계가, request_id 는 신고 한 건에서 오는 길이 쓴다.
    SELECT string_agg(want.name, ', ') INTO missing
    FROM (VALUES
        ('idx_request_metrics_created'),
        ('idx_request_metrics_request_id')
    ) AS want(name)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_class i
        JOIN pg_index x ON x.indexrelid = i.oid
        WHERE x.indrelid = relation AND i.relname = want.name
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing index on request_metrics: %', missing;
    END IF;

    -- ④ **외래 키가 없어야 한다.** chat_turns 를 CASCADE 로 걸면 탈퇴 한 번에
    --    지난달 지연 통계가 바뀐다. 대화로 안 남는 요청(라우터 실패 · 사교적 응답)도
    --    여기에는 남아야 한다.
    IF EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.contype = 'f'
    ) THEN
        RAISE EXCEPTION 'request_metrics must have no foreign key';
    END IF;

    -- ⑤ **금지된 열이 없어야 한다. 이 단언이 이 파일의 값이다.**
    --
    --    D-037 은 관측에 질문 원문을, D-054 는 관측 저장소에 회원 식별자를 금지했다.
    --    그런데 열이 하나 늘어도 스키마는 안 깨지고 테스트도 안 터진다 — 누군가
    --    "디버깅에 편하니까" 로 query 한 칸을 더하면 그날부터 이 표가 두 번째 대화
    --    저장소가 되고, 탈퇴 시 파기 대상이 하나 는다. 이름으로 막는다.
    SELECT string_agg(a.attname, ', ') INTO forbidden
    FROM pg_attribute a
    WHERE a.attrelid = relation AND a.attnum > 0 AND NOT a.attisdropped
      AND a.attname IN (
        'query', 'question', 'message', 'content', 'text', 'prompt', 'answer',
        'app_user_id', 'admin_user_id', 'user_id', 'subject', 'principal_subject',
        'lat', 'lon', 'latitude', 'longitude'
      );
    IF forbidden IS NOT NULL THEN
        RAISE EXCEPTION
            'request_metrics has forbidden column(s): % — D-037/D-054 를 보라', forbidden;
    END IF;
END
$verify$;

-- 사람이 눈으로 볼 질의. 위 단언이 통과한 뒤에만 여기 온다.
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull
FROM pg_attribute a
WHERE a.attrelid = to_regclass('request_metrics') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
