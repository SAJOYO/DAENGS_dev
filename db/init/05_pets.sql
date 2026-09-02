-- =====================================================================
-- 05_pets.sql
-- 앱 회원의 강아지 프로필
-- 실행 순서: 01_schema -> 02_trigger -> 03_auth -> 04_crawl_runs -> 05_pets
-- =====================================================================
--
-- app_users 를 FK 로 참조하므로 03_auth 뒤에 온다.

-- ---------------------------------------------------------------------
-- pets : 회원 한 명이 키우는 강아지. 여러 마리를 등록한다.
-- ---------------------------------------------------------------------
-- **개인정보 컬럼을 암호화하지 않는다.** app_users 는 이름·이메일·전화를 AES 로
-- 넣는데, 그쪽은 **사람을 식별하는 값**이라서다. 강아지 이름·견종·몸무게는 사람을
-- 가리키지 않고, 앱이 화면에 늘 띄우는 값이며, 관리 콘솔이 목록을 뽑을 때마다
-- 복호화해야 하는 비용이 이득보다 크다. 다르게 보는 시각이 있으면 PR 에서 정한다.
--
-- **마릿수 상한은 여기서 강제하지 않는다.** 트리거를 쓰면 되지만, 지금 상한(5)이
-- 임의값이고 미니룸에 몇 마리가 담기는지를 보고 정할 값이다. 서비스 계층에서
-- 막고 그 수를 한 군데(services/pet.py)에 둔다 — 바뀔 때 SQL 을 안 건드린다.
CREATE TABLE IF NOT EXISTS pets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 계정이 지워지면 강아지도 같이 지운다. 회원 탈퇴가 개인정보를 파기하는데
    -- 강아지가 남으면 파기가 반쪽이다.
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    name VARCHAR(40) NOT NULL CHECK (length(btrim(name)) > 0),

    -- 견종. **앱이 정하는 어휘라 CHECK 를 걸지 않는다.**
    -- 지금은 아바타 27종 + 'mix'(믹스·잘 모르겠어요) 인데, 그림이 늘면 목록도 는다.
    -- 여기에 CHECK 를 걸면 앱에 견종 하나 더할 때마다 DB 마이그레이션이 딸려 온다.
    breed VARCHAR(60) NOT NULL,

    -- 남/여. **NULL 은 '모름'이다.** 유기견을 데려온 경우 모를 수 있다.
    sex VARCHAR(10) CHECK (sex IN ('male','female')),

    -- 중성화. **NULL 은 '모름'이고, 모름을 false 로 바꾸지 않는다.**
    -- 안 물어본 것과 아니라고 답한 것은 다르다.
    neutered BOOLEAN,

    -- 몸무게(kg). 산책 게임·건강 조언이 쓴다.
    -- 상한은 오타를 거르는 선이다 — 세계 최대 견종도 100kg 을 넘지 않는다.
    weight_kg NUMERIC(4,1) CHECK (weight_kg > 0 AND weight_kg <= 200),

    -- 생일. **모르면 '가족이 된 날'로 대체할 수 있어서 어느 쪽인지를 같이 둔다.**
    -- 한 칸에 넣으면 "3살이에요"와 "함께한 지 2년이에요"를 구분할 수 없다.
    --   birthday    태어난 날
    --   family_day  가족이 된 날 (입양·분양일)
    -- 둘 다 모를 수 있으므로 NULL 을 허용한다. 필수로 하면 모르는 사람이 아무
    -- 날짜나 넣고, 그러면 그 값은 데이터로 못 쓴다.
    birth_date DATE,
    birth_date_kind VARCHAR(20) CHECK (birth_date_kind IN ('birthday','family_day')),

    -- 날짜와 종류는 **같이 있거나 같이 없어야 한다.** 한쪽만 있으면 "이 날짜가
    -- 무슨 날인지 모르는" 행이 생기고, 그건 안 받은 것만 못하다.
    CONSTRAINT pets_birth_date_pair
        CHECK ((birth_date IS NULL) = (birth_date_kind IS NULL)),

    -- 배웅한 날. **NULL 이면 아직 함께 있는 아이다.**
    --
    -- 삭제와 다른 일이라 칸을 따로 둔다. 목록에서 지우는 것은 없던 일로 만드는 것이고,
    -- 배웅은 있었던 일을 적어 두는 것이다 — 그래서 행을 안 지우고 이 날짜만 채운다.
    -- 앱은 이 아이를 목록에 남기고 함께한 산책과 카드도 그대로 둔다.
    --
    -- birth_date 와 같이 **시각이 아니라 날짜**다. 몇 시에 갔는지는 묻지 않는다.
    farewell_on DATE,

    -- 앞날은 못 넣는다. 오타 한 자로 2033년이 적히면 "아직 안 온 날에 배웅했다"가 된다.
    CONSTRAINT pets_farewell_not_future CHECK (farewell_on IS NULL OR farewell_on <= CURRENT_DATE),

    -- 태어나기 전일 수도 없다. 생일을 모르는 아이(birth_date IS NULL)는 이 검사에서 빠진다.
    CONSTRAINT pets_farewell_after_birth
        CHECK (farewell_on IS NULL OR birth_date IS NULL OR farewell_on >= birth_date),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 목록은 늘 "내 강아지"로 뽑는다. 등록 순서가 곧 표시 순서이고,
-- 대표를 지웠을 때 승계 대상(가장 먼저 등록한 아이)도 이 순서로 찾는다.
CREATE INDEX IF NOT EXISTS idx_pets_owner_created
    ON pets (app_user_id, created_at);

DROP TRIGGER IF EXISTS trg_pets_updated_at ON pets;
CREATE TRIGGER trg_pets_updated_at
    BEFORE UPDATE ON pets
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();


-- ---------------------------------------------------------------------
-- app_users.primary_pet_id : 대표 강아지
-- ---------------------------------------------------------------------
-- **pets 쪽에 is_primary 를 두지 않는다.** 그러면 "두 마리가 동시에 대표"를 막으려고
-- 부분 유니크 인덱스가 따로 필요하고, 대표를 바꿀 때 UPDATE 가 두 번(끄고 켜기)이다.
-- 계정 쪽에 한 칸을 두면 **한 마리만 대표인 것을 DB 가 저절로 보장**하고 바꾸는 것도
-- 한 번이다.
--
-- 강아지가 지워지면 NULL 이 된다. 남은 아이 중 하나를 자동으로 승계시키는 것은
-- 서비스 계층이 한다 — 그건 정책이지 참조 무결성이 아니다.
ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS primary_pet_id UUID
        REFERENCES pets(id) ON DELETE SET NULL;

-- 남의 강아지를 대표로 세울 수 없다는 것은 **여기서 못 막는다.**
-- FK 는 "존재하는 pets 행"까지만 보장하고 "그게 내 것인지"는 안 본다.
-- 복합 FK(app_user_id, primary_pet_id) 로 막을 수도 있지만 pets 에 UNIQUE 를
-- 하나 더 얹어야 해서, 서비스 계층에서 소유자를 확인하는 쪽을 골랐다.
