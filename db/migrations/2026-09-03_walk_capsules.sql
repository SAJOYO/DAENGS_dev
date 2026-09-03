-- 한 WalkAnalysis의 공간 기억 원판 준비 상태를 1:1 Capsule seal로 남긴다.
-- 여러 번 실행해도 기존 seal을 덮지 않는다.
BEGIN;

CREATE TABLE IF NOT EXISTS walk_capsules (
    analysis_id UUID PRIMARY KEY REFERENCES walk_analyses(id) ON DELETE CASCADE,
    capsule_version INTEGER NOT NULL,
    context_version INTEGER NOT NULL,
    capabilities JSONB NOT NULL,
    trail_context JSONB NOT NULL,
    sealed_at TIMESTAMPTZ NOT NULL,

    CONSTRAINT walk_capsules_versions_positive CHECK (
        capsule_version > 0 AND context_version > 0
    ),
    CONSTRAINT walk_capsules_capabilities_array CHECK (
        jsonb_typeof(capabilities) = 'array'
        AND jsonb_array_length(capabilities) > 0
    ),
    CONSTRAINT walk_capsules_context_object
        CHECK (jsonb_typeof(trail_context) = 'object')
);

-- 이미 finalize된 분석도 새 불변식을 만족하게 한다. 당시 Walk에 업로드된 원자만
-- 사용하며 현재 날씨나 Place/Journey를 조회해 과거를 거짓 보충하지 않는다.
INSERT INTO walk_capsules (
    analysis_id,
    capsule_version,
    context_version,
    capabilities,
    trail_context,
    sealed_at
)
SELECT
    analysis.id,
    1,
    1,
    jsonb_build_array(
        jsonb_build_object(
            'name', 'low_motion',
            'generation', analysis.observation_version
        ),
        jsonb_build_object(
            'name', 'gap',
            'generation', analysis.observation_version
        )
    ),
    jsonb_build_object(
        'context_version', 1,
        'walk_id', walk.id::text,
        'status', CASE
            WHEN walk.weather_code IS NOT NULL
                OR walk.is_day IS NOT NULL
                OR walk.temperature_c IS NOT NULL
            THEN 'partial'
            ELSE 'unknown'
        END,
        'walked_at', walk.started_at,
        'source_observed_at', NULL,
        'captured_at', analysis.derived_at,
        'provider', CASE
            WHEN walk.weather_code IS NOT NULL
                OR walk.is_day IS NOT NULL
                OR walk.temperature_c IS NOT NULL
            THEN 'legacy_walk_metadata_v1'
            ELSE NULL
        END,
        'weather_code', walk.weather_code,
        'is_day', walk.is_day,
        'temperature_c', walk.temperature_c,
        'precipitation_mm', NULL,
        'humidity_pct', NULL,
        'sun_elevation_deg', NULL,
        'failure_reason', NULL
    ),
    analysis.derived_at
FROM walk_analyses AS analysis
JOIN walks AS walk ON walk.id = analysis.walk_id
ON CONFLICT (analysis_id) DO NOTHING;

COMMENT ON TABLE walk_capsules IS
    'WalkAnalysis와 1:1인 공간 기억 Capsule seal; 원본 좌표는 별도 보존';

COMMIT;
