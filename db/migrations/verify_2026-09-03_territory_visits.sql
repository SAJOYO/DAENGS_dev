-- 2026-09-03_territory_visits.sql 적용 뒤 실행한다. 모든 결과가 0이어야 한다.

SELECT count(*) AS invalid_attempt_state
FROM territory_attempts
WHERE status NOT IN ('PENDING_UPLOAD','VISION_PENDING','VERIFIED','REJECTED','FAILED')
   OR distance_m < 0
   OR distance_m > 10
   OR is_mock
   OR photo_content_type NOT IN ('image/jpeg','image/webp');

SELECT count(*) AS terminal_photo_cleanup_pending
FROM territory_attempts
WHERE status IN ('VERIFIED', 'REJECTED', 'FAILED')
  AND photo_redacted_at IS NULL;

SELECT count(*) AS confirmed_without_photo_identity
FROM territory_attempts
WHERE status <> 'PENDING_UPLOAD'
  AND (
      photo_object_generation IS NULL
      OR btrim(photo_object_generation) = ''
      OR photo_size_bytes NOT BETWEEN 1 AND 12582912
  );

SELECT count(*) AS location_uncertainty_outside_radius
FROM territory_attempts
WHERE accuracy_m IS NULL
   OR accuracy_m < 0
   OR distance_m + accuracy_m > 10;

SELECT count(*) AS final_without_vision_metadata
FROM territory_attempts
WHERE status IN ('VERIFIED', 'REJECTED', 'FAILED')
  AND (
      vision_model IS NULL OR btrim(vision_model) = ''
      OR vision_model_version IS NULL OR btrim(vision_model_version) = ''
  );

SELECT count(*) AS verified_without_fact
FROM territory_attempts AS attempt
LEFT JOIN territory_verified_visits AS visit ON visit.attempt_id = attempt.id
WHERE attempt.status = 'VERIFIED'
  AND visit.id IS NULL;

SELECT count(*) AS fact_for_nonverified_attempt
FROM territory_verified_visits AS visit
JOIN territory_attempts AS attempt ON attempt.id = visit.attempt_id
WHERE attempt.status <> 'VERIFIED';
