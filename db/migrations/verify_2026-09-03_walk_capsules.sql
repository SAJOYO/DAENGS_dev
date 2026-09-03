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
   OR jsonb_typeof(trail_context) <> 'object'
   OR NOT trail_context ?& ARRAY[
       'context_version',
       'walk_id',
       'status',
       'walked_at',
       'source_observed_at',
       'captured_at',
       'provider',
       'weather_code',
       'is_day',
       'temperature_c',
       'precipitation_mm',
       'humidity_pct',
       'sun_elevation_deg',
       'failure_reason'
   ]
   OR jsonb_typeof(trail_context -> 'context_version') IS DISTINCT FROM 'number'
   OR jsonb_typeof(trail_context -> 'walk_id') IS DISTINCT FROM 'string'
   OR jsonb_typeof(trail_context -> 'status') IS DISTINCT FROM 'string'
   OR jsonb_typeof(trail_context -> 'walked_at') IS DISTINCT FROM 'string'
   OR jsonb_typeof(trail_context -> 'captured_at') IS DISTINCT FROM 'string';

SELECT count(*) AS mismatched_context_identity
FROM walk_capsules AS capsule
JOIN walk_analyses AS analysis ON analysis.id = capsule.analysis_id
WHERE capsule.context_version IS DISTINCT FROM CASE
        WHEN pg_input_is_valid(
            capsule.trail_context ->> 'context_version',
            'integer'
        )
        THEN (capsule.trail_context ->> 'context_version')::integer
        ELSE NULL
    END
   OR analysis.walk_id::text IS DISTINCT FROM capsule.trail_context ->> 'walk_id'
   OR CASE
        WHEN pg_input_is_valid(
            capsule.trail_context ->> 'walked_at',
            'timestamp with time zone'
        )
        THEN FALSE
        ELSE TRUE
    END
   OR CASE
        WHEN pg_input_is_valid(
            capsule.trail_context ->> 'captured_at',
            'timestamp with time zone'
        )
        THEN capsule.sealed_at <
            (capsule.trail_context ->> 'captured_at')::timestamptz
        ELSE TRUE
    END;

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
