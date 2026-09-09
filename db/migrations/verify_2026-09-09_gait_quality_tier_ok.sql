-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다. 실패 문구에는 하네스 어휘(`constraint mismatch` /
-- `missing table`)를 쓴다.
--
-- 단언은 하나다: quality_tier CHECK 가 있고, **good · ok · low 셋이 전부** 정의에 들어
-- 있어야 한다. 옛 verify(2026-09-02)는 good·low 만 "포함" 검사라 이 마이그레이션 뒤에도
-- 통과하지만, 'ok' 가 빠진 상태(=사고 이전 스키마로 되돌아간 것)는 못 잡는다 — 그것을
-- 여기서 잡는다.
DO $verify$
DECLARE
    relation regclass;
    definition text;
    missing text;
BEGIN
    IF to_regclass('gait_records') IS NULL THEN
        RAISE EXCEPTION 'missing table: gait_records';
    END IF;
    relation := to_regclass('gait_records');

    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = relation
      AND c.conname = 'gait_records_quality_tier_check'
      AND c.contype = 'c' AND c.convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'constraint mismatch: gait_records_quality_tier_check missing or not validated';
    END IF;

    -- 엔진(legacy · v4)이 내는 세 값. 하나라도 빠지면 그 구간 영상의 DONE 커밋이 죽는다.
    SELECT string_agg(v.value, ',') INTO missing
    FROM unnest(ARRAY['good','ok','low']) AS v(value)
    WHERE position('''' || v.value || '''' IN definition) = 0;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'constraint mismatch: gait_records_quality_tier_check lost (%), got %',
            missing, definition;
    END IF;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언 뒤): 지금 정의와 등급 분포.
SELECT pg_get_constraintdef(c.oid) AS quality_tier_check
FROM pg_constraint c
WHERE c.conrelid = to_regclass('gait_records') AND c.conname = 'gait_records_quality_tier_check';

SELECT coalesce(quality_tier, '(null)') AS quality_tier, count(*) AS rows
FROM gait_records GROUP BY 1 ORDER BY 2 DESC;
