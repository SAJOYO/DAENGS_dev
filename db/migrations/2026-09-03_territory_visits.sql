-- 운영 기본 DB에 점령지 방문 인증 원장과 verified visit 사실을 추가한다.
-- db/init/08_territory_visits.sql과 같은 결과이며 여러 번 실행해도 안전하다.

BEGIN;

CREATE TABLE IF NOT EXISTS territory_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    client_capture_id UUID NOT NULL,
    client_session_id UUID NOT NULL,
    site_id VARCHAR(96) NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    capture_lat NUMERIC(9, 7) NOT NULL,
    capture_lng NUMERIC(10, 7) NOT NULL,
    accuracy_m DOUBLE PRECISION,
    is_mock BOOLEAN NOT NULL DEFAULT FALSE,
    site_lat NUMERIC(9, 7) NOT NULL,
    site_lng NUMERIC(10, 7) NOT NULL,
    distance_m DOUBLE PRECISION NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'PENDING_UPLOAD',
    photo_storage_key TEXT NOT NULL UNIQUE,
    photo_content_type VARCHAR(50) NOT NULL,
    photo_deleted_at TIMESTAMPTZ,
    vision_model TEXT,
    vision_model_version TEXT,
    decision_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT territory_attempts_status_check CHECK (
        status IN ('PENDING_UPLOAD','VISION_PENDING','VERIFIED','REJECTED','FAILED')
    ),
    CONSTRAINT territory_attempts_capture_coordinate_range CHECK (
        capture_lat BETWEEN -90 AND 90 AND capture_lng BETWEEN -180 AND 180
    ),
    CONSTRAINT territory_attempts_site_coordinate_range CHECK (
        site_lat BETWEEN -90 AND 90 AND site_lng BETWEEN -180 AND 180
    ),
    CONSTRAINT territory_attempts_accuracy_nonnegative CHECK (
        accuracy_m IS NULL OR accuracy_m >= 0
    ),
    CONSTRAINT territory_attempts_distance_range CHECK (
        distance_m >= 0 AND distance_m <= 10
    ),
    CONSTRAINT territory_attempts_not_mock CHECK (is_mock = FALSE),
    CONSTRAINT territory_attempts_photo_type_check CHECK (
        photo_content_type IN ('image/jpeg','image/webp')
    ),
    CONSTRAINT territory_attempts_final_photo_deleted CHECK (
        status NOT IN ('VERIFIED','REJECTED','FAILED') OR photo_deleted_at IS NOT NULL
    ),
    CONSTRAINT territory_attempts_final_vision_metadata CHECK (
        status NOT IN ('VERIFIED','REJECTED','FAILED')
        OR (
            vision_model IS NOT NULL AND btrim(vision_model) <> ''
            AND vision_model_version IS NOT NULL AND btrim(vision_model_version) <> ''
        )
    ),
    CONSTRAINT territory_attempts_owner_capture_unique UNIQUE (
        app_user_id,
        client_capture_id
    )
);

CREATE INDEX IF NOT EXISTS territory_attempts_owner_created_idx
    ON territory_attempts (app_user_id, created_at);
CREATE INDEX IF NOT EXISTS territory_attempts_session_idx
    ON territory_attempts (app_user_id, client_session_id);

CREATE TABLE IF NOT EXISTS territory_verified_visits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL UNIQUE
        REFERENCES territory_attempts(id) ON DELETE CASCADE,
    evidence_version INTEGER NOT NULL DEFAULT 1,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT territory_verified_visits_evidence_version_positive
        CHECK (evidence_version > 0)
);

COMMENT ON TABLE territory_attempts IS
    '산책 중 점령지 촬영의 위치·업로드·VLM 상태. 실제 점령 상태가 아님';
COMMENT ON TABLE territory_verified_visits IS
    '10m 위치와 강아지 사진 판정을 모두 통과한 불변 방문 사실';

COMMIT;
