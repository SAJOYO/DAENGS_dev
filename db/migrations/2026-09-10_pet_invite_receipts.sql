-- ---------------------------------------------------------------------
-- pet_invites : 수락 영수증 (docs/co-care.md §3, 앱 계약 SAJOYO/DAENGS_dev#388 ·
--               SAJOYO/DAENGS_APP#261)
-- ---------------------------------------------------------------------
-- 이미 도는 DB 에 손으로 적용한다. 버전 테이블이 없으므로 여러 번 돌려도 안전해야 한다.
--
-- 수락이 더 이상 pet_invites 행을 지우지 않는다. 대신 accepted_at · accepted_by 를
-- 채워 "언제 누가 받았는지" 를 영수증으로 남긴다 — 응답을 못 받은 재시도(네트워크
-- 끊김)가 같은 토큰으로 다시 오면, 같은 사람에게는 같은 {pet_id, name} 을 200 으로
-- 돌려준다. 다른 사람이 이미 쓴 토큰을 보내면 여전히 404 다(그 토큰이 한 번이라도
-- 있었다는 사실이 안 새게). services/pet_member.py::accept_invite 가 그 판정이다.
--
-- 영수증의 수명은 새 칸을 안 만들고 기존 expires_at 그대로 쓴다 — 만료건 청소
-- (delete_expired_invites)가 수락 여부를 안 가리므로 이 마이그레이션으로 그 함수는
-- 안 바뀐다.
--
-- accepted_by 는 SET NULL 이다 — care_events.actor_app_user_id 와 같은 이유로,
-- 영수증(그 사람이 그날 받았다는 사실)은 행을 지우지 않고 사람만 비운다. 다만
-- app_users 는 탈퇴해도 행이 안 지워지므로(§1 "함정") 이 SET NULL 은 실질적으로
-- 거의 안 돈다 — 언젠가 행을 진짜로 지우는 날을 위한 안전망일 뿐이다.
--
-- ⚠️ ADD COLUMN ... IF NOT EXISTS 는 컬럼이 이미 있으면 그 절 전체(FK 포함)를
--    건너뛴다 — 두 번 돌려도 안전하다.
ALTER TABLE pet_invites ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ;
ALTER TABLE pet_invites ADD COLUMN IF NOT EXISTS accepted_by UUID
    REFERENCES app_users(id) ON DELETE SET NULL;
