-- =====================================================================
-- 2026-09-09_gait_records_pose_model.sql
-- gait_records.pose_model — 기록을 만든 pose model / 관절 정의 ID (D-063)
-- 적용: docker compose exec -T pgvector psql -U <user> -d vectordb -v ON_ERROR_STOP=1 < 이_파일
--       (또는 Actions 의 db-migrate.yml)
-- 여러 번 돌려도 같다 — ADD COLUMN IF NOT EXISTS, 백필은 NULL 인 행만 본다.
-- =====================================================================
--
-- 왜: 보행 엔진이 둘(legacy 12 관절 · v4 AP-10K 17 관절)이라 관절 이름이 다른 기록끼리
-- 비교하면 공통 관절이 0 이 된다. 어떤 모델로 만든 기록인지 행에 남긴다. 이 값은
-- **실행 엔진을 고르는 설정(GAIT_ENGINE)이 아니라 메타데이터**다 — 그 모델이 더는 안
-- 돌아도 옛 기록이 그 모델로 만들어진 사실은 남는다. 값의 정본은
-- backend/src/daengs_gait/contract.py 의 POSE_MODELS. CHECK 로 못 박지 않는다.
--
-- 백필 규칙 (추정 없음): summary_for_ui 의 관절 키가 **1개 이상**이고 **전부** 한 모델의
-- 관절이면 그 모델. 키가 없거나(빈 객체·NULL·비객체), 두 체계가 섞였거나, 어느 집합에도
-- 없는 키가 있으면 NULL 로 둔다. 두 집합은 서로소라 "개수 = 그 집합에 든 개수" 가 곧
-- "전부 그 집합" 이고, `n_keys >= 1` 이 빈 집합의 통과를 막는다.
--
-- 새 기록은 워커가 엔진 결과의 pose_model 을 항상 넣으므로(품질 unavailable 이어도) 여기
-- 규칙과 무관하다. 이 백필은 이미 있는 행의 NULL 만 채우고 워커가 쓴 값은 절대 덮지 않는다.
BEGIN;

-- 적용 전: 행 수만 (첫 적용에는 컬럼이 아직 없다)
SELECT count(*) AS rows_before FROM gait_records;

ALTER TABLE gait_records ADD COLUMN IF NOT EXISTS pose_model TEXT;
COMMENT ON COLUMN gait_records.pose_model IS
    '기록을 만든 pose model / 관절 정의 ID (rtmpose_ap10k_ssd · yolov8_12kp_best). '
    'NULL = 판별 불가한 옛 기록 또는 엔진 결과 없이 실패한 기록. 실행 엔진 선택 아님 (D-063).';

WITH joint_stats AS (
    SELECT g.id,
           count(joint_keys.key) AS n_keys,
           count(joint_keys.key) FILTER (WHERE joint_keys.key IN (
               'L_Eye', 'R_Eye', 'Nose', 'Neck', 'Root of tail',
               'L_Shoulder', 'L_Elbow', 'L_F_Paw', 'R_Shoulder', 'R_Elbow', 'R_F_Paw',
               'L_Hip', 'L_Knee', 'L_B_Paw', 'R_Hip', 'R_Knee', 'R_B_Paw')) AS n_ap10k,
           count(joint_keys.key) FILTER (WHERE joint_keys.key IN (
               'Ear', 'Acromion/Greater tubercle', 'Dorsal scapular spine',
               'Lateral humeral epicondyle', 'Ulnar styloid process',
               'Distal lateral aspect of fifth metacarpal bone', 'T13 Spinous precess',
               'Iliac crest', 'Femoral greater trochanter', 'Femorotibial joint',
               'Lateral malleolus of the distal tibia',
               'Distal lateral aspect of the fifth metatarsus')) AS n_legacy
    FROM gait_records AS g
    LEFT JOIN LATERAL jsonb_object_keys(
        CASE WHEN jsonb_typeof(g.summary_for_ui) = 'object' THEN g.summary_for_ui
             ELSE '{}'::jsonb END) AS joint_keys(key) ON true
    WHERE g.pose_model IS NULL
    GROUP BY g.id
)
UPDATE gait_records AS g
SET pose_model = CASE
        WHEN s.n_keys >= 1 AND s.n_keys = s.n_ap10k  THEN 'rtmpose_ap10k_ssd'
        WHEN s.n_keys >= 1 AND s.n_keys = s.n_legacy THEN 'yolov8_12kp_best'
    END
FROM joint_stats AS s
WHERE g.id = s.id
  AND (   (s.n_keys >= 1 AND s.n_keys = s.n_ap10k)
       OR (s.n_keys >= 1 AND s.n_keys = s.n_legacy));

-- 적용 후: 행 수 불변 확인(rows_before 와 같아야 한다) + 분포
SELECT count(*) AS rows_after, count(pose_model) AS filled_after FROM gait_records;
SELECT coalesce(pose_model, '(null)') AS pose_model, status, coalesce(quality_status, '(null)') AS quality_status,
       count(*) AS n
FROM gait_records GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

COMMIT;
