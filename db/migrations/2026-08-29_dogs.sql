-- 반려견 프로필 테이블 (feat: 반려견 프로필 도메인 카드).
-- 이미 돌고 있는 DB 에 손으로 적용한다. 여러 번 돌려도 안전하다 (IF NOT EXISTS).
-- 새 볼륨의 원본은 db/init/04_dogs.sql - 컬럼 설명·설계 이유는 그쪽 주석에 있다.

CREATE TABLE IF NOT EXISTS dogs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id),
    name VARCHAR(50) NOT NULL,
    breed VARCHAR(100),
    birth_date DATE,
    sex CHAR(1) CHECK (sex IN ('M', 'F')),
    neutered BOOLEAN,
    weight_kg NUMERIC(5, 2) CHECK (weight_kg > 0 AND weight_kg <= 200),
    size_class VARCHAR(10) NOT NULL
        CHECK (size_class IN ('small', 'medium', 'large')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS dogs_app_user_id_idx ON dogs (app_user_id);

DROP TRIGGER IF EXISTS trg_dogs_updated_at ON dogs;
CREATE TRIGGER trg_dogs_updated_at
    BEFORE UPDATE ON dogs
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
