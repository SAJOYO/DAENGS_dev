-- 2026-09-09_ocr_consent.sql
-- app_users 에 OCR 학습 이용 동의 두 칸을 더한다 (#353).
-- db/init/03_auth.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: vet_visits.raw_ocr_items 는 진단 추천 모델을 나중에 학습시키려고 존재한다.
-- 그것은 서비스 제공 범위 밖의 이용이라 **모으기 전에** 근거가 있어야 한다.
-- 이미 쌓인 행에 동의를 소급하는 쪽이 훨씬 비싸므로, 표를 만드는 카드와 같이 나간다.
--
-- **불리언이 아니라 시각이다.** 근거로 쓰이려면 "언제, 어느 판에" 가 남아야 하고,
-- 시각은 불리언을 포함한다 (`ocr_consent_at IS NOT NULL`).
--
-- **기본값을 걸지 않는다.** DEFAULT 를 거는 순간 아무도 누른 적 없는 동의가 전 회원에게
-- 생긴다. nullable 컬럼 둘을 더하는 것뿐이라 옛 코드에 영향이 없다.
BEGIN;

ALTER TABLE app_users ADD COLUMN IF NOT EXISTS ocr_consent_at TIMESTAMPTZ;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS ocr_consent_version VARCHAR(20);

ALTER TABLE app_users DROP CONSTRAINT IF EXISTS app_users_ocr_consent_pair;
ALTER TABLE app_users ADD CONSTRAINT app_users_ocr_consent_pair CHECK (
    (ocr_consent_at IS NULL) = (ocr_consent_version IS NULL));

COMMENT ON COLUMN app_users.ocr_consent_at IS
    'OCR 항목의 학습 이용 동의 시각. NULL 이 미동의이고 기본이다 (#353)';

COMMIT;

-- 적용 뒤 verify_2026-09-09_ocr_consent.sql 을 같은 DB 에 돌린다.
