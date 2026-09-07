-- Read-only shape checks. Execute with ON_ERROR_STOP after #260 and activity migration.
-- ⚠ 예외 문구는 하네스가 읽는 **공용 어휘**를 쓴다 — `missing table:` · `... mismatch:`.
--    `tools/check_migration_verification.py` 가 "verifier 가 잡았다"와 "적용이 실패했다"를
--    그 낱말로 가르기 때문이다. 2026-09-07(#295)에 `missing activity table:` 에서 맞췄다.
DO $$
DECLARE relation_name TEXT;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY['activity_session_links','activity_walk_heads',
        'activity_seasons','activity_accounts','activity_holding_periods',
        'activity_game_receipts','activity_bonus_keys'] LOOP
        IF to_regclass(relation_name) IS NULL THEN
            RAISE EXCEPTION 'missing table: %', relation_name;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='pets'::regclass
        AND tgname='activity_pet_cleanup' AND tgenabled='O') OR NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgrelid='territory_occupancies'::regclass
        AND tgname='activity_ownership_guard' AND tgdeferrable AND tginitdeferred AND tgenabled='O')
        OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='app_users'::regclass
            AND tgname='activity_owner_cleanup' AND tgenabled='O')
        OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='activity_holding_periods'::regclass
            AND tgname='activity_period_guard' AND tgdeferrable AND tginitdeferred AND tgenabled='O')
    THEN RAISE EXCEPTION 'trigger mismatch: activity integrity triggers missing or disabled';
    END IF;
    IF to_regclass('activity_one_active_season') IS NULL
        OR to_regclass('activity_one_open_holding') IS NULL
    THEN RAISE EXCEPTION 'index mismatch: activity unique indexes missing'; END IF;
END $$;
SELECT walk_id, analysis_id, revision, processed_revision, processed_analysis_id, contribution
FROM activity_walk_heads LIMIT 0;
SELECT id, starts_ms, ends_ms, coverage_start_ms, confirmed_ms, revision, status, rules
FROM activity_seasons LIMIT 0;
SELECT season_id, pet_id, score, final_score, revision, processed_revision, statistics
FROM activity_accounts LIMIT 0;
SELECT id, season_id, pet_id, site_id, claim_id, game_session_id, started_ms, ended_ms,
       verified_from_ms, start_order, end_order, origin, takeover FROM activity_holding_periods LIMIT 0;
