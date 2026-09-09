-- Read-only catalog + data assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마·값을 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다. 실패 문구에는 하네스 어휘(`missing table` / `column mismatch`
-- / `value mismatch`)를 쓴다.
--
-- 백필 규칙과 검증 규칙은 **다르다** (D-063):
--   백필 — 관절 키가 없으면 추정하지 않고 NULL 로 둔다.
--   검증 — pose_model 은 NULL 이거나 허용된 ID 여야 하고, 관절 키가 1개 이상이고 명확히 한
--         모델이면 그 모델의 ID 여야 한다(NULL 이면 백필 누락). **관절 키가 없는 행에는
--         NULL 을 강제하지 않는다** — 새 분석은 품질 unavailable 로 summary 가 비어도
--         엔진이 돌았으니 pose_model 을 저장하고, 그것이 정상이다.
DO $verify$
DECLARE
    relation regclass;
    bad_count bigint;
BEGIN
    IF to_regclass('gait_records') IS NULL THEN
        RAISE EXCEPTION 'missing table: gait_records';
    END IF;
    relation := to_regclass('gait_records');

    -- ⓐ 컬럼: text, nullable.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'pose_model'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'text'
          AND NOT a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: gait_records.pose_model (type text, nullable)';
    END IF;

    -- ⓑ 값은 NULL 또는 허용된 ID. 정본은 daengs_gait/contract.py 의 POSE_MODELS.
    SELECT count(*) INTO bad_count FROM gait_records
    WHERE pose_model IS NOT NULL
      AND pose_model NOT IN ('rtmpose_ap10k_ssd', 'yolov8_12kp_best');
    IF bad_count > 0 THEN
        RAISE EXCEPTION 'value mismatch: gait_records.pose_model 에 허용되지 않은 값 %행', bad_count;
    END IF;

    -- ⓒ·ⓓ 관절 키가 1개 이상이고 명확히 한 모델인 행은 그 모델의 ID 여야 한다.
    --    NULL 도 실패다 — 백필이 안 돌았거나 워커가 값을 안 넣은 것.
    WITH joint_stats AS (
        SELECT g.id, g.pose_model,
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
        GROUP BY g.id, g.pose_model
    )
    SELECT count(*) INTO bad_count FROM joint_stats
    WHERE n_keys >= 1 AND n_keys = n_ap10k
      AND pose_model IS DISTINCT FROM 'rtmpose_ap10k_ssd';
    IF bad_count > 0 THEN
        RAISE EXCEPTION 'value mismatch: AP-10K 관절 기록인데 pose_model 이 rtmpose_ap10k_ssd 가 아닌 행 %', bad_count;
    END IF;

    WITH joint_stats AS (
        SELECT g.id, g.pose_model,
               count(joint_keys.key) AS n_keys,
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
        GROUP BY g.id, g.pose_model
    )
    SELECT count(*) INTO bad_count FROM joint_stats
    WHERE n_keys >= 1 AND n_keys = n_legacy
      AND pose_model IS DISTINCT FROM 'yolov8_12kp_best';
    IF bad_count > 0 THEN
        RAISE EXCEPTION 'value mismatch: legacy 12kp 관절 기록인데 pose_model 이 yolov8_12kp_best 가 아닌 행 %', bad_count;
    END IF;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언 뒤): 분포와, 비교 가능한 품질인데 모델을 모르는 행 수(기대 0).
SELECT coalesce(pose_model, '(null)') AS pose_model, status, coalesce(quality_status, '(null)') AS quality_status,
       count(*) AS rows
FROM gait_records GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

SELECT count(*) AS done_ok_without_pose_model
FROM gait_records
WHERE status = 'DONE' AND quality_status = 'ok' AND pose_model IS NULL;
