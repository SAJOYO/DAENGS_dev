-- =====================================================================
-- verify_2026-08-31_pets.sql
-- 강아지 스키마를 **버리는 스키마에 돌려 보는** 확인용. 배포 절차가 아닙니다.
-- =====================================================================
--
-- 왜 있나: 파싱되는 SQL 과 실제로 적용되는 SQL 은 다릅니다. FK 타입이 어긋나거나
-- 실행 순서가 틀리면 여기서 걸립니다. 진짜 테이블은 건드리지 않습니다 —
-- 격리된 스키마 안에 다 만들고 끝나면 통째로 지웁니다.
--
--   docker compose exec -T pgvector psql -U postgres -d vectordb \
--       -f - < db/migrations/verify_2026-08-31_pets.sql
--
-- `daengs` 계정으로는 안 됩니다 (스키마 생성 권한이 없습니다). 그게 맞는 설정이라
-- 권한을 늘리지 말고 이 확인만 postgres 로 하세요.
\set ON_ERROR_STOP on

DROP SCHEMA IF EXISTS daengs_pets_check CASCADE;
CREATE SCHEMA daengs_pets_check;

-- public 을 뒤에 두는 이유: gen_random_uuid() 같은 함수는 public 에 있고,
-- 테이블은 이 실행 안에서 전부 새로 만들어 앞의 스키마에 들어갑니다.
SET search_path = daengs_pets_check, public;

-- 02_trigger.sql 은 public 의 documents 를 건드리므로 함수만 옮겨 왔습니다.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

\i db/init/03_auth.sql
\i db/init/05_pets.sql

-- 여러 번 돌려도 안전한지 (README 규칙)
\i db/init/05_pets.sql

-- 규칙이 실제로 도는지
DO $$
DECLARE uid UUID; pid UUID; left_id UUID; n INT;
BEGIN
    INSERT INTO app_users (kakao_id) VALUES (1) RETURNING id INTO uid;
    INSERT INTO pets (app_user_id, name, breed) VALUES (uid, '네옹', 'toy_poodle')
        RETURNING id INTO pid;
    UPDATE app_users SET primary_pet_id = pid WHERE id = uid;

    -- 날짜만 넣으면 막혀야 한다
    BEGIN
        INSERT INTO pets (app_user_id, name, breed, birth_date)
            VALUES (uid, 'x', 'y', '2024-01-01');
        RAISE EXCEPTION '날짜만 넣었는데 통과했다 — CHECK 가 안 먹는다';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE '  OK  날짜만 넣으면 막힌다';
    END;

    -- 강아지를 지우면 대표가 비어야 한다
    DELETE FROM pets WHERE id = pid;
    SELECT primary_pet_id INTO left_id FROM app_users WHERE id = uid;
    IF left_id IS NOT NULL THEN
        RAISE EXCEPTION '강아지를 지웠는데 primary_pet_id 가 남았다';
    END IF;
    RAISE NOTICE '  OK  강아지를 지우면 대표가 비워진다 (SET NULL)';

    -- 회원을 지우면 강아지도 사라져야 한다
    INSERT INTO pets (app_user_id, name, breed) VALUES (uid, '둘째', 'beagle');
    DELETE FROM app_users WHERE id = uid;
    SELECT count(*) INTO n FROM pets;
    IF n <> 0 THEN
        RAISE EXCEPTION '회원을 지웠는데 강아지가 % 마리 남았다', n;
    END IF;
    RAISE NOTICE '  OK  회원을 지우면 강아지도 사라진다 (CASCADE)';
END $$;

DROP SCHEMA daengs_pets_check CASCADE;
