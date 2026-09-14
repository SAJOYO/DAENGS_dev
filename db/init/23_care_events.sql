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
-- **비서가 이 표를 읽고, 쓴다.** 읽기는 #344 (건수·마지막 시각만 프롬프트로), 쓰기는 D-074 다.
-- 2026-09-08 의 사람 결정("채팅으로 기록하는 쓰기 능력은 처음부터 안 한다")은 2026-09-13 에
-- 개정됐다 — 같은 메모가 적어 둔 순서(로그·화면 → 기록 화면 HANDOFF → 확인 단계 있는 자동
-- 쓰기)의 세 번째 칸이고, "처음부터 안 한다" 가 가리킨 것은 **확인 없는** 쓰기였다.
--
-- 채팅 쓰기가 이 표에 넣는 값은 화면이 넣는 것과 같은 함수(`services/care_event.record`)를
-- 지난다. 다른 점은 하나뿐이다: **`client_event_id` 를 서버가 만든다.** 아래 그 칸의 주석이
-- "앱이 만든다" 고 적은 것은 여전히 앱 경로의 규칙이고, 채팅에는 그 키를 만들 앱 코드가
-- 없어서 서버의 제안 id 가 그 자리에 온다 (D-074). 유일성은 어느 쪽이 만들어도 아래 UNIQUE 가
-- 보장한다.
--
-- **개인정보 컬럼이 없다.** `pets` 와 같은 판단이다 — 밥 시각과 짧은 메모는 사람을 식별하지
-- 않는다. 메모에 사람 이름을 적는 것까지 막지는 않지만 120자라 문서가 되지 못한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS care_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- **챙긴 사람.** 소유자가 아니다 — 이 기록의 주인은 강아지다 (docs/co-care.md).
    -- 공동 돌봄에서는 대표든 돌보미든 기록할 수 있어 `pets.app_user_id` 와 같은 값이라는
    -- 보장이 없다. 챙긴 사람이 떠나도 "그날 밥을 먹은 사실" 은 강아지에 남아야 하므로
    -- NULL 을 허용한다.
    --
    -- ⚠ **그 NULL 을 넣는 것은 아래 SET NULL 이 아니다.** 탈퇴는 app_users 행을 안 지우므로
    --   (`services/app_auth.py` 의 withdraw 는 status 만 바꾼다) 이 FK 는 **영영 안 돌고**,
    --   실제로 비우는 것은 24_pet_members.sql 의 pet_membership_owner_cleanup 트리거다.
    --   SET NULL 은 언젠가 app_users 행을 진짜로 지우는 날을 위한 안전망이다 — pet_members 의
    --   CASCADE 가 그런 것과 같다. 여기에 기대면 탈퇴한 돌보미의 id 가 남의 집 케어 로그에
    --   영원히 남는다.
    --
    -- 이름을 명시하는 이유는 db/migrations/2026-09-09_pet_members.sql 이 이미 도는 DB 에서
    -- 같은 이름(care_events_actor_fkey)으로 제약을 다는 것과 맞추기 위해서다. 이름을 안
    -- 적으면 PostgreSQL 이 care_events_actor_app_user_id_fkey 로 자동 생성해 빈 볼륨과
    -- 이미 도는 DB 가 같은 FK 에 다른 이름을 갖게 된다.
    actor_app_user_id UUID
        CONSTRAINT care_events_actor_fkey REFERENCES app_users(id) ON DELETE SET NULL,

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
