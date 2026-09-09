-- ---------------------------------------------------------------------
-- care_events : 강아지별 케어 이벤트 — 밥 · 약 · 간식 (#332)
-- ---------------------------------------------------------------------
-- Order: … -> 03_auth -> 05_pets -> 23_care_events
--
-- 저장소 탭이 "개인비서" 가 되려면 오늘 밥·약·간식을 언제 챙겼는지가 남아야 한다.
-- 산책(`walks`)과 대화 요약은 있는데 밥·약·간식은 테이블도 라우트도 없었다. 이 표가 그 자리다.
--
-- **산책은 여기 안 적는다.** `walks` 가 이미 진실이라 `kind` 에 'walk' 가 없다 — 한 사실이
-- 두 곳에 있으면 반드시 어긋난다. 하루 요약(`/app/care-events/today`)이 `walks` 를 세어
-- 같이 보여 줄 뿐이다.
--
-- **비서는 이 표를 아직 안 읽는다.** 비서가 "오늘 아침 약이 아직 체크 안 됐어요" 라고 말하는
-- 것은 후속 카드다. 채팅으로 "약 먹였어" 를 **기록하는** 쓰기 능력은 처음부터 안 한다
-- (2026-09-08 사람 결정 — #331 메모): 오케스트레이터에 쓰기 능력이 하나도 없고, 오기록이
-- 로그를 조용히 망치며, 어차피 기록 화면·API 가 그 바닥이다.
--
-- **개인정보 컬럼이 없다.** `pets` 와 같은 판단이다 — 밥 시각과 짧은 메모는 사람을 식별하지
-- 않는다. 메모에 사람 이름을 적는 것까지 막지는 않지만 120자라 문서가 되지 못한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS care_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- **챙긴 사람.** 소유자가 아니다 — 이 기록의 주인은 강아지다 (docs/co-care.md).
    -- 공동 돌봄에서는 대표든 돌보미든 기록할 수 있어 `pets.app_user_id` 와 같은 값이라는
    -- 보장이 없다. `app_users` 는 탈퇴해도 안 지워지므로(위 함정과 같음) NULL 을 허용하고
    -- FK 도 SET NULL 이다 — 챙긴 사람이 떠나도 "그날 밥을 먹은 사실" 은 강아지에 남는다.
    actor_app_user_id UUID REFERENCES app_users(id) ON DELETE SET NULL,

    -- 어느 아이의 기록인가. 강아지를 지우면 기록도 같이 지운다 — 배웅(`farewell_on`)은
    -- 행을 안 지우므로 배웅한 아이의 기록은 남는다. 그것이 의도다.
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    -- 무엇을 챙겼나. **'walk' 가 없는 것이 이 표의 결정이다** (머리 주석).
    kind VARCHAR(12) NOT NULL
        CONSTRAINT care_events_kind_check CHECK (kind IN ('meal','medication','snack')),

    -- 챙긴 시각. 앱이 보낸 시각이지 서버가 받은 시각이 아니다 — "아침에 먹였는데 저녁에
    -- 적는" 경우가 있다. 받은 시각은 created_at.
    occurred_at TIMESTAMPTZ NOT NULL,

    -- 짧은 메모("사료 반만", "구토약"). 빈 문자열은 안 받는다 — 비었으면 NULL 이다.
    note VARCHAR(120)
        CONSTRAINT care_events_note_not_blank CHECK (note IS NULL OR length(btrim(note)) > 0),

    -- 멱등키. **앱이 만든다** (`walks.client_session_id` 와 같은 규칙). 탭 두 번·재시도가
    -- 두 줄이 되면 안 된다. 강아지 안에서만 유일하면 된다 — 앱은 기록 한 건을 한 아이에게
    -- 붙이므로, 다른 아이에게 같은 키가 와도 다른 기록이다.
    client_event_id UUID NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT care_events_client_event_unique UNIQUE (pet_id, client_event_id)
);

-- 하루 화면·기간 조회의 축. 최근 것이 먼저 오도록 내림차순이다.
CREATE INDEX IF NOT EXISTS idx_care_events_pet_occurred
    ON care_events (pet_id, occurred_at DESC);

COMMENT ON TABLE care_events IS
    '강아지별 케어 이벤트(밥·약·간식). 산책은 walks 가 진실이라 여기 없다 (#332)';
