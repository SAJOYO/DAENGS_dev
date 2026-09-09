-- 2026-09-08_care_events.sql
-- 강아지별 케어 이벤트(밥·약·간식) 표를 만든다 (#332).
-- db/init/23_care_events.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 저장소 탭이 "개인비서" 가 되려면 오늘 밥·약·간식을 언제 챙겼는지가 남아야 하는데
-- 서버에는 산책(walks)과 대화 요약만 있었다. 산책은 walks 가 진실이라 kind 에 'walk' 가 없다.
--
-- 새 표라 옛 앱·옛 코드에 영향이 없다 — 코드보다 먼저 적용해도 안전하다.
BEGIN;

CREATE TABLE IF NOT EXISTS care_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    kind VARCHAR(12) NOT NULL
        CONSTRAINT care_events_kind_check CHECK (kind IN ('meal','medication','snack')),
    occurred_at TIMESTAMPTZ NOT NULL,
    note VARCHAR(120)
        CONSTRAINT care_events_note_not_blank CHECK (note IS NULL OR length(btrim(note)) > 0),
    client_event_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT care_events_client_event_unique UNIQUE (pet_id, client_event_id)
);

CREATE INDEX IF NOT EXISTS idx_care_events_pet_occurred
    ON care_events (pet_id, occurred_at DESC);

COMMENT ON TABLE care_events IS
    '강아지별 케어 이벤트(밥·약·간식). 산책은 walks 가 진실이라 여기 없다 (#332)';

COMMIT;

-- 적용 뒤 verify_2026-09-08_care_events.sql 을 같은 DB 에 돌린다.
