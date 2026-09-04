-- ---------------------------------------------------------------------
-- screening_records : 피부 변화 기록 (D-052)
-- ---------------------------------------------------------------------
-- 피부 스크리닝은 원래 **판정만 하고 아무것도 안 남겼다.** `/screen/v1/screen` 이
-- 사진을 메모리에서 받아 결과를 돌려주고 끝이었고, 인증도 없었다. 그래서 "지난번보다
-- 나아졌나" 를 볼 수가 없었다 — 그게 이 표가 생긴 이유다.
--
-- ⚠️ **옛 경로 `/screen/v1/screen` 은 그대로 둔다.** 여기 쓰는 것은 backend 가 소유하는
--    새 계약 `/app/screening/*` 이다. 보행이 `/gait/*` → `/app/gait/*` 로 옮길 때와
--    같은 방식으로, 앱이 옮겨간 뒤에 옛 경로를 410 으로 닫는다 (D-043 선례).
--
-- ⚠️ **사진을 지우지 않는다.** 점령지 사진은 판정이 끝나면 0바이트로 덮지만(redact),
--    여기는 **사진 자체가 기록**이다 — 지난 사진과 나란히 놓고 보는 것이 이 기능이다.
--    공개한 처리방침 1항 수집 항목표에 "사진(피부 상태 확인)" 이 이미 있고, 보관은
--    4항의 "탈퇴 시 지체 없이 파기" 하나뿐이다 (D-052 B).

CREATE TABLE IF NOT EXISTS screening_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 누구의 기록인가.
    --
    -- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴는 app_users 행을 **남기므로**
    --    (services/app_auth.py: kakao_id 로 "이미 탈퇴한 사람"을 알아보려고) 이
    --    CASCADE 는 영영 안 돈다. 탈퇴 경로가 명시로 지운다 — 대화(chats)가 같은
    --    이유로 명시 삭제인 것과 같다.
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    -- 어느 아이의 피부인가. **NULL 을 허용한다** — 아이를 지워도 기록은 남길 수 있게.
    -- (아이를 지우면 SET NULL 이고, 사진은 탈퇴 때 지운다)
    pet_id UUID REFERENCES pets(id) ON DELETE SET NULL,

    -- 파이프라인 상태. 전이는 services/screening.py 만 한다.
    --   PENDING_UPLOAD  티켓만 발급됨. 아직 사진이 안 올라왔다
    --   DONE            사진이 올라오고 판정까지 끝났다
    --   FAILED          사진은 올라왔는데 판정이 실패했다 (가중치 없음 등)
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING_UPLOAD'
        CHECK (status IN ('PENDING_UPLOAD','DONE','FAILED')),

    -- 사진이 있는 곳. 키는 **backend 가 만든다**(D-043 원칙 6) — 앱이 정하면 남의
    -- 경로를 덮어쓰거나 훔쳐볼 수 있다. 키에 uuid 가 들어가는 것도 그래서다:
    -- bridge 는 인증 헤더 없이 "키를 아는 것이 자격" 이라 추측 가능하면 안 된다.
    photo_storage_key VARCHAR(200) NOT NULL,
    photo_content_type VARCHAR(40) NOT NULL
        CHECK (photo_content_type IN ('image/jpeg','image/webp')),

    -- 저장소 세대값. 로컬 볼륨은 sha256 hex, GCS 는 숫자 문자열이라 **문자로 받는다.**
    -- confirm 이 고정한 바이트를 가리킨다.
    photo_generation VARCHAR(64),
    photo_size_bytes INTEGER CHECK (photo_size_bytes IS NULL OR photo_size_bytes > 0),

    -- 앱의 **가이드 프레임** (정규화 [x, y, w, h]). 학습과 같은 함수로 자르는 데 쓴다.
    -- 없으면 화면 중앙으로 물러선다 — 1단계는 큰 차이가 없지만 2단계 분포가 학습
    -- 크롭과 어긋난다 (daengs_screening/service.py 주석).
    box JSONB,

    -- 판정 결과 원본 (`agent.screen` 이 돌려준 그대로).
    --
    -- ⚠️ **표를 열로 펼치지 않는다.** 모델 계약(CONTRACT_VERSION)이 바뀌면 열이
    --    따라 바뀌어야 하는데, 기록은 옛 계약으로 판정된 것도 그대로 남아야 한다.
    --    무엇으로 판정했는지는 아래 contract_version 이 들고 있다.
    result JSONB,

    -- 판정 당시의 모델 계약 버전. 옛 기록을 새 화면이 잘못 읽지 않게 하는 열쇠다.
    contract_version VARCHAR(20),

    -- 실패했으면 왜. 사용자에게 그대로 보여 주지 않는다 (운영자용).
    failure_reason TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 변화 기록은 늘 "이 아이의 것을 최근 순으로" 뽑는다.
CREATE INDEX IF NOT EXISTS idx_screening_records_pet_created
    ON screening_records (pet_id, created_at DESC);

-- 아이를 안 고른 기록도 내 것끼리는 모아 봐야 한다.
CREATE INDEX IF NOT EXISTS idx_screening_records_owner_created
    ON screening_records (app_user_id, created_at DESC);

-- bridge 가 키 하나로 행을 찾는 자리. 키는 전역 유일해야 한다 —
-- 같은 키를 두 행이 가리키면 어느 쪽 소유인지 갈리지 않는다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_screening_records_photo_key
    ON screening_records (photo_storage_key);

DROP TRIGGER IF EXISTS trg_screening_records_updated_at ON screening_records;
CREATE TRIGGER trg_screening_records_updated_at
    BEFORE UPDATE ON screening_records
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
