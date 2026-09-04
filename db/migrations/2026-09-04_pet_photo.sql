-- 2026-09-04_pet_photo.sql
-- 강아지에게 **프로필 사진 자리**를 더한다.
-- db/init/05_pets.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 앱에 프로필 사진이 이미 있는데(`pet/PetPhotos.kt`) 서버에 자리가 없어서
-- **기기 안에만** 있다. 그 클래스 주석이 "폰을 바꾸면 사진이 사라지고 견종 그림으로
-- 되돌아간다" 라고 적어 둔 상태였다. D-052 로 공용 파일 저장소가 정해졌으니 옮긴다.
--
-- 사진 자체는 DB 에 안 들어간다. 저장소(gait-bridge 볼륨)에 두고 여기에는 어디에
-- 있는지와 그것이 정말 그 사진인지만 적는다.
--
-- ⚠️ 이 마이그레이션만으로는 사진이 안 켜진다. 저장소가 꺼져 있으면(`GAIT_STORAGE=none`)
--    티켓 발급이 503 이다 — 서버 .env 세 줄이 같이 필요하다 (D-052).

BEGIN;

ALTER TABLE pets ADD COLUMN IF NOT EXISTS photo_storage_key VARCHAR(200);
ALTER TABLE pets ADD COLUMN IF NOT EXISTS photo_content_type VARCHAR(40);
ALTER TABLE pets ADD COLUMN IF NOT EXISTS photo_generation VARCHAR(64);
ALTER TABLE pets ADD COLUMN IF NOT EXISTS photo_size_bytes INTEGER;
ALTER TABLE pets ADD COLUMN IF NOT EXISTS photo_updated_at TIMESTAMPTZ;
ALTER TABLE pets ADD COLUMN IF NOT EXISTS photo_pending_key VARCHAR(200);
ALTER TABLE pets ADD COLUMN IF NOT EXISTS photo_pending_content_type VARCHAR(40);
ALTER TABLE pets ADD COLUMN IF NOT EXISTS photo_pending_at TIMESTAMPTZ;

COMMENT ON COLUMN pets.photo_storage_key IS
    '확정된 프로필 사진의 저장소 키. NULL 이면 사진이 없고 앱이 견종 그림을 쓴다';
COMMENT ON COLUMN pets.photo_generation IS
    '저장소 세대값. 로컬 볼륨은 sha256 hex, GCS 는 숫자 문자열이라 문자로 받는다';
COMMENT ON COLUMN pets.photo_pending_key IS
    '티켓을 끊어 줬지만 아직 안 올라온 키. bridge PUT 이 이 값으로 자격을 확인한다';

-- 확정 사진의 칸들은 같이 있거나 같이 없어야 한다. 하나만 남으면 "어디 있는지는
-- 아는데 그게 무엇인지 모르는" 행이 되고, 그건 안 받은 것만 못하다.
--
-- **DO 블록으로 감싸는 이유**는 ALTER TABLE ADD CONSTRAINT 에 IF NOT EXISTS 가
-- 없기 때문이다 (pets_farewell_* 과 같은 방식).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pets_photo_set') THEN
        ALTER TABLE pets ADD CONSTRAINT pets_photo_set CHECK (
            (photo_storage_key IS NULL) = (photo_content_type IS NULL)
            AND (photo_storage_key IS NULL) = (photo_generation IS NULL)
            AND (photo_storage_key IS NULL) = (photo_size_bytes IS NULL)
            AND (photo_storage_key IS NULL) = (photo_updated_at IS NULL)
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pets_photo_pending_set') THEN
        ALTER TABLE pets ADD CONSTRAINT pets_photo_pending_set CHECK (
            (photo_pending_key IS NULL) = (photo_pending_content_type IS NULL)
            AND (photo_pending_key IS NULL) = (photo_pending_at IS NULL)
        );
    END IF;

    -- 앱이 그릴 수 있는 형식만 받는다. 저장소 키의 확장자도 이 값에서 정해진다.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pets_photo_content_type') THEN
        ALTER TABLE pets ADD CONSTRAINT pets_photo_content_type CHECK (
            photo_content_type IS NULL OR photo_content_type IN ('image/jpeg','image/webp')
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pets_photo_pending_content_type'
    ) THEN
        ALTER TABLE pets ADD CONSTRAINT pets_photo_pending_content_type CHECK (
            photo_pending_content_type IS NULL
            OR photo_pending_content_type IN ('image/jpeg','image/webp')
        );
    END IF;

    -- 0바이트는 redact() 가 남기는 tombstone 과 같은 모양이라 확정 사진일 수 없다.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pets_photo_size') THEN
        ALTER TABLE pets ADD CONSTRAINT pets_photo_size CHECK (
            photo_size_bytes IS NULL OR photo_size_bytes > 0
        );
    END IF;
END $$;

-- bridge 가 키 하나로 행을 찾는 두 자리다. 업로드는 대기 키로, 내려받기는 확정 키로
-- 찾는다. 부분 인덱스인 이유는 사진이 선택이라 대부분의 행이 NULL 이기 때문이다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pets_photo_pending_key
    ON pets (photo_pending_key) WHERE photo_pending_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_pets_photo_storage_key
    ON pets (photo_storage_key) WHERE photo_storage_key IS NOT NULL;

COMMIT;
