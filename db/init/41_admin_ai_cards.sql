-- ---------------------------------------------------------------------
-- admin_ai_cards : 관리자 콘솔이 시험 삼아 뽑은 도감 카드 (#592, docs/cardimage/)
-- ---------------------------------------------------------------------
-- 앱 표(`ai_cards`)와 **칸을 하나도 공유하지 않는다.** 저쪽은 앱 사용자가 만든 카드라
-- 하루 한도 · 동시 생성 방어 · 탈퇴 정리가 줄줄이 걸려 있는데, 이 표는 그중 어느 것도
-- 걸지 않는다. 콘솔에서 사람이 눈으로 보려고 뽑는 것이라서다.
--
-- 그래서 status 도 없다 — 콘솔 생성은 **동기**다(nginx 300s). 다 만들어진 카드만 이 표에
-- 들어오므로 'generating' 같은 중간 상태가 생기지 않고, 이미지 칸이 전부 NOT NULL 이다.
--
-- ⚠️ **목록은 관리자 전원이 공유한다.** `admin_user_id` 는 "누가 뽑았나"를 적는 칸이지
--    조회 범위를 가르는 칸이 아니다 (사용자 결정 09-18).
--
-- ⚠️ **원본 사진은 저장하지 않는다.** 만들어진 카드 PNG 만 저장소에 남는다.
--
-- ⚠️ **CASCADE 에 기대도 되는 자리다** — 관리자 계정은 `status` 로 막지 행을 지우지 않으므로
--    이 CASCADE 는 거의 돌지 않는다. 다만 돌더라도 저장소 객체는 FK 가 없어 남는다.

CREATE TABLE IF NOT EXISTS admin_ai_cards (
    id UUID PRIMARY KEY,
    -- 누가 뽑았나. 목록을 가르지는 않는다 (위 주석).
    admin_user_id UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    -- 달이면 "1".."12", 종류면 "strawberry" · "lettuce" (`daengs_cardimage.catalog.card_key`).
    -- 정수가 아니다 — 달이 아닌 카드가 같은 칸에 들어온다.
    card_key VARCHAR(20) NOT NULL,
    -- 카드에 **인쇄된** 이름과 제목. 나중에 무엇을 고쳐도 이 글자는 안 바뀐다.
    dog_name VARCHAR(40) NOT NULL,
    title VARCHAR(80) NOT NULL,
    -- 어느 엔진으로 뽑았나. 'gemini'(Nano Banana 2) · 'cardgen'(FLUX.2-klein-4B).
    engine VARCHAR(20) NOT NULL,
    -- GPU 경로에서만 쓴다. SMALLINT 가 아니다 — seed 는 32767 을 넘을 수 있다 (ai_cards 와 같다).
    seed INTEGER,
    attempts SMALLINT NOT NULL,
    -- 검수 결과. 검수를 끄고 뽑으면 둘 다 비어 있다.
    likeness SMALLINT,
    judge_note VARCHAR(200),
    -- 동기 생성이라 이 아래는 전부 채워진다 — 저장이 안 됐으면 행 자체를 안 만든다.
    storage_key VARCHAR(200) NOT NULL,
    size_bytes INTEGER NOT NULL,
    -- SMALLINT 가 아니다 — 앞으로 틀이 커져도 칸부터 고치는 일이 없게 INTEGER 로 둔다.
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    -- 생성에 걸린 시간. 엔진을 서로 견줄 때 사람이 보는 숫자다.
    elapsed_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT admin_ai_cards_engine CHECK (engine IN ('gemini', 'cardgen')),
    CONSTRAINT admin_ai_cards_card_key CHECK (length(btrim(card_key)) > 0),
    CONSTRAINT admin_ai_cards_likeness CHECK (likeness IS NULL OR likeness BETWEEN 1 AND 5),
    CONSTRAINT admin_ai_cards_size CHECK (size_bytes > 0)
);

-- 목록은 늘 "관리자 전원의 카드를 최근 것부터". **DESC 를 잃으면** 오래된 것부터 읽는
-- 정렬이 되어 기본 50건이 엉뚱한 쪽을 준다 (인덱스가 있으니 에러는 안 난다).
CREATE INDEX IF NOT EXISTS idx_admin_ai_cards_created
    ON admin_ai_cards (created_at DESC);

-- 저장 키는 카드 하나에 하나다. `ai_cards` 와 달리 부분 인덱스가 아니다 — 이 표의
-- storage_key 는 NOT NULL 이라 거를 것이 없다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_admin_ai_cards_storage_key
    ON admin_ai_cards (storage_key);
