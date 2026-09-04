-- 2026-09-04_dog_cards.sql
-- 도감 카드 표를 만든다.
-- db/init/10_dog_cards.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 뽑은 카드가 앱의 Room 과 `filesDir/cards/<id>.png` 에만 있어서 **폰을 바꾸면
-- 전부 사라졌다.** D-052 로 저장소가 정해졌으니 올린다.
--
-- ⚠️ **id 를 서버가 만들지 않는다.** 앱이 만든 UUID 를 그대로 받는다 — 앱이 그렇게
--    설계해 뒀다("서버가 붙어도 이 id 를 그대로 올려서 재전송이 멱등해진다").
--    그래서 이 표의 쓰기는 PUT upsert 다.

BEGIN;

CREATE TABLE IF NOT EXISTS dog_cards (
    -- **앱이 만든 UUID.** 저장소의 얼굴 파일 이름도 이 값에서 나온다.
    id UUID PRIMARY KEY,

    -- 누구 것인가.
    --
    -- 앱에서는 이 칸이 NULL 일 수 있다 — "둘러보기로 들어와 로그인 없이 뽑은 카드"다.
    -- **서버에는 그 상태로 올라오지 않는다**: 올리려면 로그인해야 하고, 앱은 다음
    -- 로그인 때 그 카드들을 계정에 귀속시킨 뒤 올린다. 그래서 여기는 NOT NULL 이다.
    --
    -- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴는 app_users 행을 **남기므로**
    --    (kakao_id 로 "이미 탈퇴한 사람"을 알아보려고) 이 CASCADE 는 영영 안 돈다.
    --    탈퇴 경로가 명시로 지운다 — 대화(chats)·피부 기록과 같다.
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    -- 어느 야채인가. **번호가 아니라 문자열이다.**
    -- 카드 목록의 원본이 저쪽 저장소의 `cards.mjs` 라, 저쪽이 순서를 바꾸면 번호가
    -- 통째로 밀린다 — 그러면 **어제 뽑은 배추가 오늘 피망이 된다** (앱 주석).
    template_id VARCHAR(80) NOT NULL CHECK (length(btrim(template_id)) > 0),

    -- 어느 아이로 뽑았나. **아이를 지워도 카드는 남는다** (SET NULL).
    dog_id UUID REFERENCES pets(id) ON DELETE SET NULL,

    -- 카드에 **인쇄된** 이름. dog_id 와 따로 두는 것이 모순처럼 보이지만 성질이 다르다 —
    -- 이건 "그 아이의 지금 이름"이 아니라 그때 카드에 찍힌 글자다. **개명했다고 이미
    -- 뽑아 놓은 카드의 인쇄가 바뀌면 안 된다** (앱 주석).
    dog_name VARCHAR(40) NOT NULL,

    -- 뽑은 시각. 앱은 epoch millis 로 들고 있고 올릴 때 시각으로 바꾼다.
    drawn_at TIMESTAMPTZ NOT NULL,

    -- 번호판 글자. 생일에서 만든다.
    code_text VARCHAR(40) NOT NULL DEFAULT '',

    -- **사용자가 원형 틀에 직접 맞춘 카드인가.**
    -- 예전에는 앱이 알아서 얼굴을 구멍에 끼웠고(1.15배 확대 + 턱 걸기), 그건 사용자가
    -- 못 보고 맡겼을 때 필요한 보정이다. 직접 맞춘 카드에 그 보정을 또 걸면 **본 것과
    -- 다르게 나온다.** false 인 옛 카드는 예전 규칙 그대로 그린다 — **이미 뽑아 둔
    -- 카드가 업데이트로 달라지면 안 된다** (앱 주석).
    user_framed BOOLEAN NOT NULL DEFAULT FALSE,

    -- 또렷한 얼굴만의 자리. **이게 없으면 다음에 열 때 얼굴이 밀린다** — 누끼는 목
    -- 아래가 서서히 흐려지며 끝나는데, 그 꼬리까지 포함한 사각형을 구멍에 맞추면
    -- 머리가 반대쪽으로 밀리고 구멍 한쪽이 통째로 빈다 (앱 주석).
    -- 앱과 같이 네 칸으로 펴 둔다.
    core_left INTEGER NOT NULL,
    core_top INTEGER NOT NULL,
    core_right INTEGER NOT NULL,
    core_bottom INTEGER NOT NULL,

    -- 사각형이 뒤집히면 그리는 쪽에서 조용히 이상해진다. 여기서 막는다.
    CONSTRAINT dog_cards_core_rect CHECK (core_right > core_left AND core_bottom > core_top),

    -- 얼굴 그림. **PNG 뿐이다** — 구멍에 끼우려면 알파가 필요해서 JPEG 은 못 쓴다.
    -- NULL 이면 아직 안 올라온 것이고, 그때 앱은 자기 기기의 파일을 쓴다.
    face_storage_key VARCHAR(200),
    face_generation VARCHAR(64),
    face_size_bytes INTEGER CHECK (face_size_bytes IS NULL OR face_size_bytes > 0),

    -- 얼굴 칸들은 같이 있거나 같이 없어야 한다.
    CONSTRAINT dog_cards_face_set CHECK (
        (face_storage_key IS NULL) = (face_generation IS NULL)
        AND (face_storage_key IS NULL) = (face_size_bytes IS NULL)
    ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 도감은 늘 "내 카드를 뽑은 순서로" 펼친다.
CREATE INDEX IF NOT EXISTS idx_dog_cards_owner_drawn
    ON dog_cards (app_user_id, drawn_at DESC);

-- "이 야채를 몇 장 가졌나" 는 도감 화면이 매번 묻는다.
CREATE INDEX IF NOT EXISTS idx_dog_cards_owner_template
    ON dog_cards (app_user_id, template_id);

-- bridge 가 키 하나로 행을 찾는 자리. 전역 유일해야 한다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_dog_cards_face_key
    ON dog_cards (face_storage_key) WHERE face_storage_key IS NOT NULL;

DROP TRIGGER IF EXISTS trg_dog_cards_updated_at ON dog_cards;
CREATE TRIGGER trg_dog_cards_updated_at
    BEFORE UPDATE ON dog_cards
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE dog_cards IS
    '뽑아 놓은 도감 카드. id 는 앱이 만든다 (오프라인에서 먼저 만들어지므로) — D-052';
COMMENT ON COLUMN dog_cards.dog_name IS
    '카드에 인쇄된 이름. 개명해도 안 바뀐다 — 그때 찍힌 글자다';
COMMENT ON COLUMN dog_cards.user_framed IS
    '사용자가 원형 틀에 직접 맞췄나. false 인 옛 카드는 예전 규칙 그대로 그린다';
COMMENT ON COLUMN dog_cards.face_storage_key IS
    '누끼 딴 얼굴 PNG. 원본 사진은 저장하지 않는다';

COMMIT;
