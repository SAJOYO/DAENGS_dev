-- 2026-09-09_gait_quality_tier_ok.sql
-- gait_records.quality_tier 의 CHECK 에 'ok' 를 더한다 — db/init/07_gait_records.sql 과
-- 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** DROP IF EXISTS 뒤 같은 이름으로 다시 만든다. 이 저장소는
-- 버전 테이블이 없어서 DB 가 적용 여부를 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 보행 엔진은 legacy(daengs_gait/quality_gate.py)·v4(gait_v4/quality.py) 둘 다 유효
-- 프레임 수로 good(>80) / ok(20~80) / low(<20) 세 단계를 내고, 앱(GaitQualityTier)도 세
-- 단계를 그린다. 그런데 CHECK 는 D-043 때 good/low 둘만 적혔다(엔진 코드가 아니라 짐작으로).
-- 20~80 구간 영상이 한 주 동안 한 건도 없어 숨어 있다가, 2026-09-09 47.mp4(유효 59) 에서
-- DONE 커밋이 CheckViolation 으로 죽고 행이 PROCESSING 으로 남았다(두 번 연속).
--
-- 기존 행은 전부 새 CHECK 를 만족한다(good · low · NULL 뿐) — 데이터 변환은 일어나지 않는다.
-- 이 파일이 하는 일은 제약 정의 하나를 바꾸는 것뿐이다.
--
-- ⚠ 적용 순서: **이 CHECK 확장이 먼저, 그 다음 코드(gait-worker) 배포.** 반대로 하면
--   새 코드가 'ok' 를 그대로 쓰다 옛 CHECK 에 막혀 같은 좀비가 재발한다.

BEGIN;

ALTER TABLE gait_records DROP CONSTRAINT IF EXISTS gait_records_quality_tier_check;
ALTER TABLE gait_records ADD CONSTRAINT gait_records_quality_tier_check
    CHECK (quality_tier IN ('good','ok','low'));

COMMIT;
