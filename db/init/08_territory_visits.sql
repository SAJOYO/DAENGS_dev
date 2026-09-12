-- 점령지 방문 인증. 시도 상태와 검증 완료 사실을 분리한다.
-- 실제 점령/소유권은 이 테이블의 책임이 아니며 후속 정책이 verified visit을 소비한다.

CREATE TABLE IF NOT EXISTS territory_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    -- 앱 로컬 DB의 재시도 키와 산책 세션 키. 촬영 중에는 서버 walks 행이 아직 없다.
    client_capture_id UUID NOT NULL,
    client_session_id UUID NOT NULL,

    site_id VARCHAR(96) NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    capture_lat NUMERIC(9, 7) NOT NULL,
    capture_lng NUMERIC(10, 7) NOT NULL,
    accuracy_m DOUBLE PRECISION NOT NULL,
    is_mock BOOLEAN NOT NULL DEFAULT FALSE,

    -- 판정 당시 게임판 좌표. 게임판 세대가 바뀌어도 과거 10m 판정을 재현한다.
    site_lat NUMERIC(9, 7) NOT NULL,
    site_lng NUMERIC(10, 7) NOT NULL,
    distance_m DOUBLE PRECISION NOT NULL,

    status VARCHAR(24) NOT NULL DEFAULT 'PENDING_UPLOAD',
    photo_storage_key TEXT NOT NULL UNIQUE,
    photo_content_type VARCHAR(50) NOT NULL,
    photo_object_generation TEXT,
    photo_size_bytes BIGINT,
    photo_redacted_at TIMESTAMPTZ,

    vision_model TEXT,
    vision_model_version TEXT,
    decision_reason TEXT,

    vision_lease_token UUID,
    vision_lease_until TIMESTAMPTZ,
    vision_attempts INTEGER NOT NULL DEFAULT 0,
    vision_available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    vision_dispatch_after TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    vision_retry_reason TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT territory_attempts_status_check CHECK (
        status IN ('PENDING_UPLOAD','VISION_PENDING','VERIFIED','REJECTED','FAILED')
    ),
    CONSTRAINT territory_vision_attempts_check CHECK (vision_attempts >= 0 AND vision_attempts <= 2),
    CONSTRAINT territory_vision_lease_check CHECK (
        (vision_lease_token IS NULL AND vision_lease_until IS NULL)
        OR (vision_lease_token IS NOT NULL AND vision_lease_until IS NOT NULL AND status = 'VISION_PENDING')
    ),
    CONSTRAINT territory_attempts_capture_coordinate_range CHECK (
        capture_lat BETWEEN -90 AND 90 AND capture_lng BETWEEN -180 AND 180
    ),
    CONSTRAINT territory_attempts_site_coordinate_range CHECK (
        site_lat BETWEEN -90 AND 90 AND site_lng BETWEEN -180 AND 180
    ),
    CONSTRAINT territory_attempts_location_evidence CHECK (
        accuracy_m >= 0 AND distance_m + accuracy_m <= 10
    ),
    CONSTRAINT territory_attempts_distance_range CHECK (
        distance_m >= 0 AND distance_m <= 10
    ),
    CONSTRAINT territory_attempts_not_mock CHECK (is_mock = FALSE),
    CONSTRAINT territory_attempts_photo_type_check CHECK (
        photo_content_type IN ('image/jpeg','image/webp')
    ),
    CONSTRAINT territory_attempts_confirmed_photo_identity CHECK (
        status = 'PENDING_UPLOAD'
        OR (
            photo_object_generation IS NOT NULL
            AND btrim(photo_object_generation) <> ''
            AND photo_size_bytes > 0
            AND photo_size_bytes <= 12582912
        )
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
CREATE INDEX IF NOT EXISTS territory_vision_dispatch_idx
    ON territory_attempts (vision_dispatch_after, id)
    WHERE status = 'VISION_PENDING'
       OR (status IN ('VERIFIED','REJECTED','FAILED') AND photo_redacted_at IS NULL);

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
    '앱 위치 attestation의 보수적 10m 조건과 강아지 사진 판정을 통과한 방문 사실';
