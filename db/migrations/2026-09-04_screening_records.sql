-- 2026-09-04_screening_records.sql
-- 피부 **변화 기록** 표를 만든다.
-- db/init/09_screening_records.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 피부 스크리닝은 **판정만 하고 아무것도 안 남겼다.** `/screen/v1/screen` 이 사진을
-- 메모리에서 받아 결과를 돌려주고 끝이었고 인증도 없었다. 그래서 "지난번보다 나아졌나"
-- 를 볼 수가 없었다. D-052 로 저장소가 정해졌으니 사진과 결과를 남긴다.
--
-- ⚠️ 옛 경로 `/screen/v1/screen` 은 **안 건드린다.** 지금 앱이 그대로 쓴다.
--    새 계약은 `/app/screening/*` 이고, 앱이 옮겨간 뒤 옛 경로를 410 으로 닫는 것은
--    별도 카드다 (보행의 `/gait/*` → `/app/gait/*` 와 같은 방식).

BEGIN;

CREATE TABLE IF NOT EXISTS screening_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- ⚠️ CASCADE 에 기대면 안 된다. 탈퇴는 app_users 행을 남기므로 이 CASCADE 는
    --    영영 안 돈다 — 탈퇴 경로가 명시로 지운다 (chats 와 같은 이유).
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    -- 아이를 지워도 기록은 남을 수 있게 NULL 을 허용한다.
    pet_id UUID REFERENCES pets(id) ON DELETE SET NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'PENDING_UPLOAD'
        CHECK (status IN ('PENDING_UPLOAD','DONE','FAILED')),

    photo_storage_key VARCHAR(200) NOT NULL,
    photo_content_type VARCHAR(40) NOT NULL
        CHECK (photo_content_type IN ('image/jpeg','image/webp')),
    photo_generation VARCHAR(64),
    photo_size_bytes INTEGER CHECK (photo_size_bytes IS NULL OR photo_size_bytes > 0),

    box JSONB,
    result JSONB,
    contract_version VARCHAR(20),
    failure_reason TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE screening_records IS
    '피부 변화 기록. 사진 자체가 기록이라 판정 뒤에도 지우지 않는다 (D-052)';
COMMENT ON COLUMN screening_records.result IS
    'agent.screen 원본. 열로 펼치지 않는다 — 옛 계약으로 판정된 기록도 그대로 남아야 한다';
COMMENT ON COLUMN screening_records.photo_generation IS
    '저장소 세대값. 로컬 볼륨은 sha256 hex, GCS 는 숫자 문자열이라 문자로 받는다';

CREATE INDEX IF NOT EXISTS idx_screening_records_pet_created
    ON screening_records (pet_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_screening_records_owner_created
    ON screening_records (app_user_id, created_at DESC);

-- 키는 전역 유일해야 한다 — 같은 키를 두 행이 가리키면 어느 쪽 소유인지 갈리지 않는다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_screening_records_photo_key
    ON screening_records (photo_storage_key);

-- set_updated_at() 은 db/init/02_trigger.sql 이 만든다. 이미 돌고 있는 DB 에는 있다.
DROP TRIGGER IF EXISTS trg_screening_records_updated_at ON screening_records;
CREATE TRIGGER trg_screening_records_updated_at
    BEFORE UPDATE ON screening_records
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

COMMIT;
