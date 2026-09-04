-- 2026-09-02_walk_analyses.sql
-- 봉인된 산책 입력의 버전별 계산 결과와 compact Cellophane을 저장한다.
-- db/init/06_walks.sql 과 같은 결과가 되게 하며 여러 번 실행해도 안전하다.

BEGIN;

ALTER TABLE walks ADD COLUMN IF NOT EXISTS analysis_state VARCHAR(16);
UPDATE walks SET analysis_state = 'collecting' WHERE analysis_state IS NULL;
ALTER TABLE walks ALTER COLUMN analysis_state SET DEFAULT 'collecting';
ALTER TABLE walks ALTER COLUMN analysis_state SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'walks_analysis_state_check'
          AND conrelid = 'public.walks'::regclass
    ) THEN
        ALTER TABLE walks ADD CONSTRAINT walks_analysis_state_check
            CHECK (analysis_state IN ('collecting', 'derived'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS walk_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    walk_id UUID NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    input_fingerprint VARCHAR(71) NOT NULL,
    point_count INTEGER NOT NULL,
    terminal_client_seq INTEGER,
    facts_record_version INTEGER NOT NULL,
    calculation_version INTEGER NOT NULL,
    receipt_version INTEGER NOT NULL,
    observation_version INTEGER NOT NULL,
    moving_distance_m INTEGER NOT NULL,
    moving_s INTEGER NOT NULL,
    stop_count INTEGER NOT NULL,
    facts JSONB NOT NULL,
    measurement_receipt JSONB NOT NULL,
    motion_events JSONB NOT NULL,
    micro_observations JSONB NOT NULL,
    derived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT walk_analyses_input_fingerprint_check
        CHECK (input_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT walk_analyses_point_count_check CHECK (point_count >= 0),
    CONSTRAINT walk_analyses_terminal_sequence_check CHECK (
        (point_count = 0 AND terminal_client_seq IS NULL)
        OR (point_count > 0 AND terminal_client_seq = point_count - 1)
    ),
    CONSTRAINT walk_analyses_versions_positive CHECK (
        facts_record_version > 0
        AND calculation_version > 0
        AND receipt_version > 0
        AND observation_version > 0
    ),
    CONSTRAINT walk_analyses_summary_nonnegative CHECK (
        moving_distance_m >= 0 AND moving_s >= 0 AND stop_count >= 0
    ),
    CONSTRAINT walk_analyses_facts_object CHECK (jsonb_typeof(facts) = 'object'),
    CONSTRAINT walk_analyses_receipt_object
        CHECK (jsonb_typeof(measurement_receipt) = 'object'),
    CONSTRAINT walk_analyses_events_array CHECK (jsonb_typeof(motion_events) = 'array'),
    CONSTRAINT walk_analyses_observations_array
        CHECK (jsonb_typeof(micro_observations) = 'array'),
    CONSTRAINT walk_analyses_identity_unique UNIQUE (
        walk_id,
        input_fingerprint,
        facts_record_version,
        calculation_version,
        receipt_version,
        observation_version
    )
);

CREATE INDEX IF NOT EXISTS walk_analyses_walk_derived_idx
    ON walk_analyses (walk_id, derived_at DESC);

CREATE TABLE IF NOT EXISTS walk_cellophane_sheets (
    analysis_id UUID NOT NULL REFERENCES walk_analyses(id) ON DELETE CASCADE,
    paint_fp VARCHAR(128) NOT NULL,
    sheet_schema_version INTEGER NOT NULL,
    paint_version INTEGER NOT NULL,
    grid_version VARCHAR(64) NOT NULL,
    radius_u DOUBLE PRECISION NOT NULL,
    profile VARCHAR(128) NOT NULL,
    profile_fp VARCHAR(128) NOT NULL,
    sample_step_m DOUBLE PRECISION NOT NULL,
    cell_count INTEGER NOT NULL,
    sheet_fingerprint VARCHAR(71) NOT NULL,
    payload JSONB NOT NULL,
    derived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT walk_cellophane_versions_positive
        CHECK (sheet_schema_version > 0 AND paint_version > 0),
    CONSTRAINT walk_cellophane_spec_positive CHECK (radius_u > 0 AND sample_step_m > 0),
    CONSTRAINT walk_cellophane_identity_nonempty CHECK (
        paint_fp <> '' AND grid_version <> '' AND profile <> '' AND profile_fp <> ''
    ),
    CONSTRAINT walk_cellophane_cell_count_check CHECK (cell_count >= 0),
    CONSTRAINT walk_cellophane_fingerprint_check
        CHECK (sheet_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT walk_cellophane_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (analysis_id, paint_fp)
);

CREATE INDEX IF NOT EXISTS walk_cellophane_paint_fp_idx
    ON walk_cellophane_sheets (paint_fp);

COMMENT ON COLUMN walks.analysis_state IS
    'collecting이면 입력 변경 가능, derived이면 현재 입력 봉인. 계산 세대는 walk_analyses가 소유';
COMMENT ON TABLE walk_analyses IS
    '봉인된 산책 입력을 버전된 계산 계약으로 해석한 불변 결과';
COMMENT ON TABLE walk_cellophane_sheets IS
    '분석 결과를 Paint spec으로 칠한 정렬 cell 배열 JSONB. 셀 검색 index는 아직 만들지 않음';

COMMIT;
