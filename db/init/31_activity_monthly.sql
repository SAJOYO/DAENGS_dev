-- Only explicitly enrolled seasons have automatic monthly successors.
CREATE TABLE IF NOT EXISTS activity_monthly_seasons (
    season_id TEXT PRIMARY KEY REFERENCES activity_seasons(id) ON DELETE CASCADE,
    previous_season_id TEXT UNIQUE REFERENCES activity_seasons(id),
    CHECK (previous_season_id IS NULL OR previous_season_id <> season_id)
);
ALTER TABLE activity_accounts ADD COLUMN IF NOT EXISTS final_rank BIGINT;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='activity_accounts'::regclass AND conname='activity_final_rank_positive') THEN
        ALTER TABLE activity_accounts ADD CONSTRAINT activity_final_rank_positive CHECK (final_rank > 0);
    END IF;
END $$;
