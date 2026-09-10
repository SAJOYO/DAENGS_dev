-- First-season reward storage only. Never creates a season or rewrites its rules.
-- Apply after 21_activity_game.sql. Member eligibility survives deletion of a pet.
CREATE TABLE IF NOT EXISTS activity_base_rewards (
    season_id TEXT NOT NULL REFERENCES activity_seasons(id) ON DELETE CASCADE,
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    site_id VARCHAR(96) NOT NULL REFERENCES territory_claim_sites(site_id),
    paid BIGINT NOT NULL DEFAULT 0 CHECK (paid IN (0,20,100)),
    PRIMARY KEY (season_id,app_user_id,site_id)
);
CREATE INDEX IF NOT EXISTS activity_base_rewards_member ON activity_base_rewards(app_user_id);

CREATE TABLE IF NOT EXISTS activity_reward_details (
    season_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    app_user_id UUID NOT NULL,
    site_id VARCHAR(96) NOT NULL,
    reward_version TEXT NOT NULL CHECK (reward_version='first-season-rewards-v1'),
    base_before BIGINT NOT NULL CHECK (base_before IN (0,20,100)),
    base_after BIGINT NOT NULL CHECK (base_after IN (0,20,100)),
    base_points BIGINT NOT NULL CHECK (base_points >= 0),
    takeover_points BIGINT NOT NULL CHECK (takeover_points IN (0,20)),
    PRIMARY KEY (season_id,event_id),
    FOREIGN KEY (season_id,event_id) REFERENCES activity_game_receipts(season_id,event_id)
        ON DELETE CASCADE,
    FOREIGN KEY (season_id,app_user_id,site_id)
        REFERENCES activity_base_rewards(season_id,app_user_id,site_id) ON DELETE CASCADE,
    CHECK (base_after=base_before+base_points)
);

-- Withdrawal retains app_users, so explicit cleanup is needed even when game is OFF.
CREATE OR REPLACE FUNCTION activity_reward_owner_cleanup() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status='withdrawn' THEN
        PERFORM pg_advisory_xact_lock(260,36);
        DELETE FROM activity_base_rewards WHERE app_user_id=NEW.id;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS activity_reward_owner_cleanup ON app_users;
CREATE TRIGGER activity_reward_owner_cleanup AFTER UPDATE OF status ON app_users
FOR EACH ROW EXECUTE FUNCTION activity_reward_owner_cleanup();
