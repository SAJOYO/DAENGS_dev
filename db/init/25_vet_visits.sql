-- 25_vet_visits.sql
-- 영수증 사진에서 읽은 진료비 기록(vet_visits)과, 유저가 확정하기 전의 초안(vet_visit_drafts) (#353).
-- db/migrations/2026-09-09_vet_visits.sql 과 같은 결과가 되게 한다.
--
-- **표가 둘인 것이 이 설계의 핵심이다.** OCR·LLM 이 추측한 진단 라벨은 vet_visit_drafts 에만
-- 있고, 유저가 [확인] 을 누른 순간에만 vet_visits 로 넘어온다. 채팅 맥락도 저장소 목록도
-- vet_visits 만 읽으므로, 확정 안 된 추측은 **거르는 게 아니라 거기 없다.** 한 표에
-- status 칸을 두고 WHERE 로 거르는 쪽은 언젠가 그 WHERE 를 빠뜨리는 조회가 생기는데,
-- 그 자리가 하필 프롬프트면 기계가 지어낸 병명이 의료 기록처럼 읽힌다.

BEGIN;

-- ── 확정된 진료 기록 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vet_visits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    -- 영수증에 찍힌 **날짜**다. 시각은 안 남긴다 — care_events 는 "아침 약" 을 가르려고
    -- occurred_at 을 시각으로 두지만, 진료비에 시분은 아무 질문도 안 가른다.
    visited_on DATE NOT NULL,

    -- 원(KRW) 정수. 부동소수도 NUMERIC 도 아니다 — 원에는 보조단위가 없다.
    -- 상한 1억은 OCR 이 자릿수를 흘리는 실패(15,000 을 150,000 으로)의 **터무니없는 쪽만**
    -- 걸러낸다. 그럴듯하게 틀린 값은 오직 확인 화면이 잡는다.
    total_krw INTEGER NOT NULL
        CONSTRAINT vet_visits_total_krw_range CHECK (total_krw >= 0 AND total_krw <= 100000000),

    -- 병원 정보. 셋 다 NULL 가능하다 — 영수증이 흐리면 못 읽는다.
    -- 전화번호만 모양을 건다: 이 칸은 앱에서 `tel:` 링크가 되므로 사람이 눌러 전화를 건다.
    -- 숫자와 하이픈만 허용해 "010-0000-0000 (원장님 개인)" 같은 값이 링크가 되는 것을 막는다.
    hospital_name VARCHAR(60)
        CONSTRAINT vet_visits_hospital_name_not_blank
        CHECK (hospital_name IS NULL OR length(btrim(hospital_name)) > 0),
    hospital_address VARCHAR(200)
        CONSTRAINT vet_visits_hospital_address_not_blank
        CHECK (hospital_address IS NULL OR length(btrim(hospital_address)) > 0),
    hospital_phone VARCHAR(32)
        CONSTRAINT vet_visits_hospital_phone_shape
        CHECK (hospital_phone IS NULL OR hospital_phone ~ '^[0-9]{2,4}(-[0-9]{3,4}){1,2}$'),

    -- 유저가 확정한 사유. **닫힌 목록이고, 축이 하나다.** 자유 텍스트면 피부염·피부질환·
    -- 피부병이 서로 다른 키가 되어 "피부로 1년간 얼마" 가 영영 안 모인다 — 사유별 누계가
    -- 이 기능의 존재 이유이므로 그 집계가 서는 쪽을 고른다. 사람의 말은 reason_detail 이 받는다.
    --
    -- 축은 "이 방문이 무엇을 겨눴나" 하나다 — 신체계통 열둘, 아니면 예방 넷(겨누는 계통이
    -- 없다), 아니면 other. **병리(종양·외상)와 응급도를 코드에서 뺀 것이 이 목록의 요점이다.**
    -- 그 둘은 방문이 겨눈 대상이 아니라 방문의 성질이라, 코드에 섞으면 한 방문에 코드가
    -- 둘씩 맞아떨어진다 — 피부 종괴 제거가 skin 이자 tumor 이고, 야간 골절이 injury 이자
    -- musculoskeletal 이자 emergency 다. 같은 병이 방문마다 다른 칸에 떨어지면 지키려던
    -- 누계가 바로 그 지점에서 조용히 깨진다. 응급도는 is_emergency, 종양은 is_oncology 로
    -- 뺐다 (아래).
    reason_code VARCHAR(20) NOT NULL
        CONSTRAINT vet_visits_reason_code_check CHECK (reason_code IN (
            'skin','ear','eye','dental','digestive','respiratory','cardiac',
            'urinary','reproductive','musculoskeletal','neurologic','endocrine',
            'vaccination','parasite_prevention','checkup','neuter','other')),

    -- 유저가 덧붙인 한 줄("왼쪽 뒷다리"). 집계에 안 쓰고 프롬프트에도 안 간다.
    reason_detail VARCHAR(60)
        CONSTRAINT vet_visits_reason_detail_not_blank
        CHECK (reason_detail IS NULL OR length(btrim(reason_detail)) > 0),

    -- 기계가 뭐라고 제안했는지. NULL 이면 제안이 없었다는 뜻(사진이 안 읽혔거나 손입력).
    -- **label_source 칸을 따로 두지 않는다** — 이 칸과 reason_code 를 비교하면
    -- 그대로 나오기 때문이다: NULL 이면 제안 없음, 같으면 수용, 다르면 유저가 고침.
    suggested_reason_code VARCHAR(20)
        CONSTRAINT vet_visits_suggested_reason_code_check CHECK (
            suggested_reason_code IS NULL OR suggested_reason_code IN (
            'skin','ear','eye','dental','digestive','respiratory','cardiac',
            'urinary','reproductive','musculoskeletal','neurologic','endocrine',
            'vaccination','parasite_prevention','checkup','neuter','other')),

    -- 응급 방문이었나. **코드가 아니라 칸인 이유**는 응급이 겨눈 대상이 아니라 방문의
    -- 성질이어서다. 부수 효과가 하나 있다 — 이 칸은 이 표에서 OCR 이 **진단보다 더 확실하게**
    -- 읽는 값이다: 야간진료비·응급진료비·공휴일 할증이 영수증에 항목으로 찍혀 있어서,
    -- 판단이 아니라 글자를 읽으면 된다.
    is_emergency BOOLEAN NOT NULL DEFAULT false,

    -- 종양 진료였나. 응급과 같은 이유로 칸이고, **다른 점은 기계가 못 채운다는 것이다** —
    -- 이것은 영수증에 찍힌 글자가 아니라 임상 판단이라 추출 스키마에 칸을 두지 않는다
    -- (docs/vet-visits.md §2). 확인 화면의 체크 하나로 유저만 켠다.
    is_oncology BOOLEAN NOT NULL DEFAULT false,

    -- 추출된 진료 항목 [{"name": "초진료", "amount_krw": 15000}, ...].
    -- **보호자 이름·전화·카드번호는 여기 없다** — 추출 스키마에 그 칸 자체가 없다.
    raw_ocr_items JSONB NOT NULL DEFAULT '[]'::jsonb
        CONSTRAINT vet_visits_raw_ocr_items_is_array
        CHECK (jsonb_typeof(raw_ocr_items) = 'array'),

    -- 영수증 사진. 손입력이면 NULL 이다. key 는 backend 가 만든다 (core/storage 원칙 6).
    receipt_image_key TEXT
        CONSTRAINT vet_visits_receipt_image_key_not_blank
        CHECK (receipt_image_key IS NULL OR length(btrim(receipt_image_key)) > 0),

    -- 멱등키. care_events 는 (pet_id, ...) 로 묶지만 여기는 **유저 단위**로 묶는다 —
    -- 재시도 도중 활성 강아지가 바뀌면 pet 단위 키는 두 줄을 허용하는데, 유저 단위는 막는다.
    client_event_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT vet_visits_client_event_unique UNIQUE (app_user_id, client_event_id)
);

-- 사유별 누계(최근 12개월)와 마지막 방문이 둘 다 이 인덱스로 간다.
CREATE INDEX IF NOT EXISTS idx_vet_visits_pet_visited
    ON vet_visits (pet_id, visited_on DESC);

COMMENT ON TABLE vet_visits IS
    '영수증에서 읽고 유저가 확정한 진료비 기록. 확정 전 추측은 vet_visit_drafts 에 있다';

-- ── 확정 전 초안 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vet_visit_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    receipt_image_key TEXT NOT NULL
        CONSTRAINT vet_visit_drafts_receipt_image_key_not_blank
        CHECK (length(btrim(receipt_image_key)) > 0),

    -- 추출 결과 통째로. **칼럼으로 안 쪼갠다** — 확정 전 값이라 조회할 일이 없고,
    -- 쪼개는 순간 vet_visits 와 같은 모양이 두 벌 생겨 한쪽만 고치는 날이 온다.
    extracted JSONB
        CONSTRAINT vet_visit_drafts_extracted_is_object
        CHECK (extracted IS NULL OR jsonb_typeof(extracted) = 'object'),
    extracted_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 24시간 지난 초안을 지우는 청소가 이 인덱스로 간다 (docs/vet-visits.md "초안 청소").
CREATE INDEX IF NOT EXISTS idx_vet_visit_drafts_created_at
    ON vet_visit_drafts (created_at);

COMMENT ON TABLE vet_visit_drafts IS
    '확정 전 진료비 초안. 채팅도 목록도 이 표를 안 읽는다 — 24시간 뒤 사진과 함께 지운다';

COMMIT;
