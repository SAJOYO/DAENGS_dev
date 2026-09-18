-- ---------------------------------------------------------------------
-- ai_cards : 사진 한 장으로 서버가 만든 도감 카드 (#537, D-076 · D-085, docs/cardimage/)
-- ---------------------------------------------------------------------
-- **달(1~12)만이 아니다** — 종류 카드(딸기·상추)도 이 표에 들어온다 (#593, D-085).
-- 무엇을 만들었는지는 `card_key` 가 갖고, `month` 는 달 카드에만 있다.
-- dog_cards(앱이 누끼 얼굴을 끼워 만든 카드)와 **별개다.** 이건 서버가 만든 카드 한 장 통째
-- PNG(994×1582) 이고, 그래서 id 도 서버가 만든다(POST).
--
-- 생성은 30~60초 걸리는 유료 호출이라 비동기다: POST 가 이 행을 'generating' 으로 커밋하고
-- 202 를 준 뒤, backend 프로세스 안의 백그라운드 작업이 'ready' 나 'failed' 로 바꾼다.
-- 배포 재시작과 겹쳐 사라진 작업은 조회 때 'failed' + error_code 'interrupted' 로 정리된다.
--
-- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴는 app_users 행을 남기므로 이 CASCADE 는 영영 안 돈다 —
--    탈퇴 경로가 명시로 지운다(dog_cards 와 같다). 저장소 객체는 FK 가 없어 더더욱 그렇다.
--
-- ⚠️ **원본 사진은 저장하지 않는다.** 백그라운드에 메모리로 넘기고 끝이다.

CREATE TABLE IF NOT EXISTS ai_cards (
    id UUID PRIMARY KEY,
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    -- 어느 아이로 만들었나. **아이를 지워도 카드는 남는다** (SET NULL).
    dog_id UUID REFERENCES pets(id) ON DELETE SET NULL,
    -- 달 카드의 달(1~12). **종류 카드(딸기·상추)는 NULL 이다** — 그 카드에는 달이 없다 (D-085).
    month SMALLINT,
    -- 무엇을 만들었나. 달이면 "1".."12", 종류면 "strawberry" · "lettuce"
    -- (`daengs_cardimage.catalog.card_key`, admin_ai_cards.card_key 와 같은 모양).
    -- 정수가 아니다 — 달이 아닌 카드가 같은 칸에 들어온다. 한도(강아지당 카드 종류 1장)가 이 값을 센다.
    card_key VARCHAR(20) NOT NULL,
    -- 카드에 **인쇄된** 이름. 개명해도 이미 만든 카드의 글자는 안 바뀐다.
    dog_name VARCHAR(40) NOT NULL,
    title VARCHAR(80) NOT NULL,
    status VARCHAR(16) NOT NULL,
    error_code VARCHAR(32),
    -- 아래 칸들은 'ready' 가 되어야 채워진다.
    storage_key VARCHAR(200),
    generation VARCHAR(64),
    size_bytes INTEGER,
    width SMALLINT,
    height SMALLINT,
    likeness SMALLINT,
    attempts SMALLINT,
    -- 이 카드를 만든 seed. 같은 seed 가 같은 자리를 깨뜨리므로(#557 E1) 기록해 둔다.
    -- SMALLINT 가 아니다 — seed 는 32767 을 넘을 수 있다.
    seed INTEGER,
    -- 같은 요청에서 나온 장들을 묶는다(#572 Task 4). 사용자가 하나를 고르면 나머지 형제 행은
    -- 지운다. 요청의 대표(첫) 행은 자기 id 를 그대로 쓴다(pick_group = id, fix round 1
    -- Critical) — 그래야 idx_ai_cards_one_generating 이 그 한 행만 보고 「사용자별 동시 1
    -- 요청」을 지킬 수 있다. NULL 은 이 기능이 생기기 전 옛 행에만 남는다.
    pick_group UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 달 카드와 종류 카드를 한 표에 담는 규칙 (D-085). 달이 있으면 1~12 이고 card_key 가 그
    -- 달의 문자열이어야 하며, 달이 없으면(종류 카드) card_key 는 숫자가 아니어야 한다 —
    -- 숫자를 막지 않으면 "month 는 비었는데 card_key 가 '4'" 인 행이 설 수 있고, 그건 앱에
    -- month=null 로 나가는 달 카드다(전환기 계약이 month 를 함께 싣는다).
    CONSTRAINT ai_cards_month CHECK (
        (month IS NOT NULL AND month BETWEEN 1 AND 12 AND card_key = month::text)
        OR (month IS NULL AND card_key !~ '^[0-9]+$')
    ),
    CONSTRAINT ai_cards_card_key CHECK (length(btrim(card_key)) > 0),
    CONSTRAINT ai_cards_dog_name CHECK (length(btrim(dog_name)) > 0),
    CONSTRAINT ai_cards_status CHECK (status IN ('generating', 'ready', 'failed')),
    -- 'ready' 인데 이미지가 없으면 앱이 빈 카드를 받는다.
    CONSTRAINT ai_cards_ready_set CHECK (
        status <> 'ready' OR (
            storage_key IS NOT NULL AND generation IS NOT NULL AND size_bytes IS NOT NULL
            AND width IS NOT NULL AND height IS NOT NULL
        )
    ),
    -- 실패에는 이유가 있고, 실패가 아니면 이유가 없다.
    CONSTRAINT ai_cards_failed_code CHECK ((status = 'failed') = (error_code IS NOT NULL)),
    CONSTRAINT ai_cards_size CHECK (size_bytes IS NULL OR size_bytes > 0),
    CONSTRAINT ai_cards_likeness CHECK (likeness IS NULL OR likeness BETWEEN 1 AND 5),
    CONSTRAINT ai_cards_attempts CHECK (attempts IS NULL OR attempts BETWEEN 1 AND 2)
);

-- 목록은 늘 "내 카드를 최근 것부터", 한도는 "내 오늘 카드 수".
CREATE INDEX IF NOT EXISTS idx_ai_cards_owner_created
    ON ai_cards (app_user_id, created_at DESC);

-- bridge 가 키 하나로 행을 찾는 자리. 전역 유일해야 한다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_cards_storage_key
    ON ai_cards (storage_key) WHERE storage_key IS NOT NULL;

-- **사용자별 동시 요청 1개를 DB 가 보장한다** (행 1개가 아니다 — fix round 1 Critical). 앱이 두
-- 번 누른 요청이 동시에 들어와도 한 요청만 선다. 한 요청의 행은 전부 pick_group 을 공유하고
-- 대표 행만 id = pick_group 이라 이 인덱스에 걸린다 — 그래서 같은 요청의 형제 행 여럿이 동시에
-- generating 이어도 막히지 않는다. WHERE 가 빠지면 카드를 평생 한 장밖에 못 만든다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_cards_one_generating
    ON ai_cards (app_user_id) WHERE status = 'generating' AND id = pick_group;

-- 「고른 카드만 남기고 형제를 지운다」 가 pick_group 으로 형제를 찾을 때 쓴다.
CREATE INDEX IF NOT EXISTS ix_ai_cards_pick_group
    ON ai_cards (pick_group);

DROP TRIGGER IF EXISTS trg_ai_cards_updated_at ON ai_cards;
CREATE TRIGGER trg_ai_cards_updated_at
    BEFORE UPDATE ON ai_cards
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
