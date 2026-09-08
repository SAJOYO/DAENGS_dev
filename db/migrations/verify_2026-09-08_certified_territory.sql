-- Read-only verification; fails rather than silently accepting an incomplete rollout.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'territory_occupancies'
        AND column_name = 'certified_at' AND data_type = 'timestamp with time zone') THEN
        RAISE EXCEPTION 'territory_occupancies.certified_at missing';
    END IF;
    IF to_regclass('territory_challenges') IS NULL THEN
        RAISE EXCEPTION 'territory_challenges missing';
    END IF;
    IF EXISTS (SELECT 1 FROM territory_occupancies WHERE certification = 'VERIFIED' AND certified_at IS NULL) THEN
        RAISE EXCEPTION 'verified occupancy backfill incomplete';
    END IF;
END $$;
