-- =====================================================================
-- 02_trigger.sql
-- updated_at 자동 갱신 함수 / 트리거
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1) 함수 : UPDATE 직전에 updated_at을 현재 시각으로 덮어쓴다
--    - NEW      : 이번 UPDATE로 저장될 새 행
--    - RETURN NEW : 생략하면 UPDATE 자체가 무시되므로 필수
--    - 테이블마다 만들 필요 없이 하나만 만들어 재사용한다
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ---------------------------------------------------------------------
-- 2) 트리거 : documents 테이블에 위 함수를 연결
--    - BEFORE UPDATE : 저장 전이어야 NEW 값을 고칠 수 있음 (AFTER는 불가)
--    - FOR EACH ROW  : 수정된 행마다 1회 실행
-- ---------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;

CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();