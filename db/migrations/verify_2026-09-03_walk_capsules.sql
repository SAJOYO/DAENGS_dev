-- 2026-09-03_walk_capsules.sql 적용 뒤 psql에서 실행한다.
-- 첫 결과가 모두 0이고 마지막 표의 형식/출처를 눈으로 확인한다.

SELECT count(*) AS derived_without_capsule
FROM walk_analyses AS analysis
LEFT JOIN walk_capsules AS capsule ON capsule.analysis_id = analysis.id
WHERE capsule.analysis_id IS NULL;

SELECT count(*) AS invalid_capsule_shape
FROM walk_capsules
WHERE capsule_version <= 0
   OR context_version <= 0
   OR jsonb_typeof(capabilities) <> 'array'
   OR jsonb_array_length(capabilities) = 0
   OR jsonb_typeof(trail_context) <> 'object';

SELECT count(*) AS mismatched_context_identity
FROM walk_capsules AS capsule
JOIN walk_analyses AS analysis ON analysis.id = capsule.analysis_id
WHERE capsule.context_version <> (capsule.trail_context ->> 'context_version')::integer
   OR analysis.walk_id::text <> capsule.trail_context ->> 'walk_id'
   OR capsule.sealed_at < (capsule.trail_context ->> 'captured_at')::timestamptz;

SELECT
    analysis.walk_id,
    capsule.analysis_id,
    capsule.capsule_version,
    capsule.trail_context ->> 'status' AS context_status,
    capsule.trail_context ->> 'provider' AS context_provider,
    capsule.sealed_at
FROM walk_capsules AS capsule
JOIN walk_analyses AS analysis ON analysis.id = capsule.analysis_id
ORDER BY capsule.sealed_at DESC
LIMIT 10;
