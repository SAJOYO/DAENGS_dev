-- =====================================================================
-- 04_dogs.sql
-- 반려견 프로필 - DAENGS 의 canonical Dog Profile
-- 02_trigger.sql 의 set_updated_at() 을 재사용하므로 02 보다 뒤여야 한다.
-- 03_auth.sql 의 app_users 를 참조하므로 03 보다 뒤여야 한다.
-- =====================================================================

-- ---------------------------------------------------------------------
-- dogs : app_user 가 소유하는 반려견 프로필
--
-- Place 검색 게이트웨이가 여기서 size/weight/age 를 projection 하고(#63 의
-- place-search 는 identity 를 받지 않는다), 에이전트의 "프로필 + 진료 이력
-- 주입"도 장차 같은 행을 읽는다. Place 요구에 스키마를 역으로 맞추지 않는다.
--
-- 암호화하지 않는다 - D-012 의 대상은 사람의 개인정보(이메일·전화·이름)이고,
-- 개의 이름·생일은 그 축이 아니다. 이 판단이 바뀌면 app_users 처럼 *_enc 로.
-- ---------------------------------------------------------------------
CREATE TABLE dogs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 소유자. 탈퇴는 행 삭제가 아니라 app_users.status 로 표현되므로 CASCADE 없음.
    app_user_id UUID NOT NULL REFERENCES app_users(id),

    name VARCHAR(50) NOT NULL,

    -- 자유 입력. 품종 사전을 강제하지 않는다 - 믹스·미상이 흔하다.
    breed VARCHAR(100),

    -- 나이는 저장하지 않는다. 저장하는 순간부터 낡는다 - 소비자가 여기서 계산한다.
    birth_date DATE,

    sex CHAR(1) CHECK (sex IN ('M', 'F')),
    neutered BOOLEAN,

    weight_kg NUMERIC(5, 2) CHECK (weight_kg > 0 AND weight_kg <= 200),

    -- 무게에서 파생하지 않고 따로 받는다. 크기 등급은 무게의 함수가 아니라
    -- 보호자가 아는 사실이다 - 같은 9kg 도 견종에 따라 small/medium 이 갈린다.
    size_class VARCHAR(10) NOT NULL
        CHECK (size_class IN ('small', 'medium', 'large')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- "내 강아지 목록"이 유일한 목록 조회 경로다.
CREATE INDEX dogs_app_user_id_idx ON dogs (app_user_id);

-- updated_at 트리거 (함수는 02_trigger.sql 의 set_updated_at())
DROP TRIGGER IF EXISTS trg_dogs_updated_at ON dogs;
CREATE TRIGGER trg_dogs_updated_at
    BEFORE UPDATE ON dogs
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
