-- Depends on 06_walks.sql (analyses/capsules) and #260 20_territory_claims.sql.
-- No season is created or enabled by this migration. Safe to re-run.
CREATE TABLE IF NOT EXISTS activity_session_links (
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    client_session_id UUID NOT NULL,
    walk_id UUID UNIQUE REFERENCES walks(id) ON DELETE SET NULL,
    game_session_id UUID UNIQUE REFERENCES territory_claim_sessions(id) ON DELETE SET NULL,
    PRIMARY KEY(app_user_id,client_session_id)
);
CREATE TABLE IF NOT EXISTS activity_walk_heads (
    walk_id UUID PRIMARY KEY REFERENCES walks(id) ON DELETE CASCADE,
    analysis_id UUID NOT NULL UNIQUE REFERENCES walk_analyses(id) ON DELETE CASCADE,
    revision BIGINT NOT NULL CHECK(revision > 0),
    processed_revision BIGINT NOT NULL DEFAULT 0 CHECK(processed_revision >= 0),
    processed_analysis_id UUID REFERENCES walk_analyses(id) ON DELETE SET NULL,
    contribution JSONB,
    CHECK(processed_revision <= revision)
);
CREATE TABLE IF NOT EXISTS activity_seasons (
    id TEXT PRIMARY KEY CHECK(btrim(id) <> ''),
    starts_ms BIGINT NOT NULL CHECK(starts_ms >= 0),
    ends_ms BIGINT NOT NULL CHECK(ends_ms > starts_ms),
    coverage_start_ms BIGINT NOT NULL,
    confirmed_ms BIGINT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0 CHECK(revision >= 0),
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','FINALIZED')),
    rules JSONB NOT NULL,
    CHECK(coverage_start_ms >= starts_ms AND coverage_start_ms < ends_ms),
    CHECK(confirmed_ms >= coverage_start_ms AND confirmed_ms <= ends_ms)
);
CREATE UNIQUE INDEX IF NOT EXISTS activity_one_active_season
ON activity_seasons(status) WHERE status='ACTIVE';
CREATE TABLE IF NOT EXISTS activity_accounts (
    season_id TEXT NOT NULL REFERENCES activity_seasons(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    score JSONB NOT NULL,
    final_score JSONB,
    revision BIGINT NOT NULL CHECK(revision > 0),
    processed_revision BIGINT NOT NULL DEFAULT 0 CHECK(processed_revision >= 0),
    statistics JSONB,
    PRIMARY KEY(season_id,pet_id),
    CHECK(processed_revision <= revision)
);
CREATE TABLE IF NOT EXISTS activity_holding_periods (
    id UUID PRIMARY KEY,
    season_id TEXT NOT NULL REFERENCES activity_seasons(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    site_id VARCHAR(96) NOT NULL REFERENCES territory_claim_sites(site_id),
    claim_id UUID REFERENCES territory_claims(id) ON DELETE SET NULL,
    game_session_id UUID REFERENCES territory_claim_sessions(id) ON DELETE SET NULL,
    started_ms BIGINT NOT NULL,
    ended_ms BIGINT,
    verified_from_ms BIGINT,
    start_order BIGINT NOT NULL,
    end_order BIGINT,
    origin TEXT NOT NULL CHECK(origin IN ('ACQUIRED','IMPORTED')),
    takeover BOOLEAN NOT NULL,
    CHECK(ended_ms IS NULL OR ended_ms >= started_ms),
    CHECK(verified_from_ms IS NULL OR verified_from_ms >= started_ms),
    CHECK((ended_ms IS NULL) = (end_order IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS activity_one_open_holding
ON activity_holding_periods(season_id,site_id) WHERE ended_ms IS NULL;
CREATE INDEX IF NOT EXISTS activity_holding_pet ON activity_holding_periods(season_id,pet_id);
CREATE TABLE IF NOT EXISTS activity_game_receipts (
    season_id TEXT NOT NULL REFERENCES activity_seasons(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    claim_id UUID NOT NULL REFERENCES territory_claims(id) ON DELETE CASCADE,
    bonus BIGINT NOT NULL CHECK(bonus >= 0),
    at_ms BIGINT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY(season_id,event_id)
);
CREATE TABLE IF NOT EXISTS activity_bonus_keys (
    season_id TEXT NOT NULL REFERENCES activity_seasons(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    site_id VARCHAR(96) NOT NULL REFERENCES territory_claim_sites(site_id),
    utc_day BIGINT NOT NULL,
    PRIMARY KEY(season_id,pet_id,site_id,utc_day)
);

-- Retain other dogs' histories without keeping the deleted dog's ID inside JSON/arrays.
CREATE OR REPLACE FUNCTION activity_pet_cleanup() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(260,36);
    UPDATE territory_claim_sites SET version=version+1 WHERE site_id IN (
        SELECT o.site_id FROM territory_occupancies o JOIN territory_claims c ON c.id=o.claim_id
        WHERE c.pet_id=OLD.id
    );
    UPDATE territory_claim_sessions SET pet_ids=array_remove(pet_ids,OLD.id)
        WHERE OLD.id=ANY(pet_ids);
    RETURN OLD;
END $$;
DROP TRIGGER IF EXISTS activity_pet_cleanup ON pets;
CREATE TRIGGER activity_pet_cleanup BEFORE DELETE ON pets
FOR EACH ROW EXECUTE FUNCTION activity_pet_cleanup();

-- app_users survives withdrawal. Privacy cleanup must still run with the feature
-- disabled after its migration has been installed.
CREATE OR REPLACE FUNCTION activity_owner_cleanup() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status='withdrawn' THEN
        PERFORM pg_advisory_xact_lock(260,36);
        DELETE FROM activity_session_links WHERE app_user_id=NEW.id;
        DELETE FROM territory_claim_sessions WHERE app_user_id=NEW.id;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS activity_owner_cleanup ON app_users;
CREATE TRIGGER activity_owner_cleanup AFTER UPDATE OF status ON app_users
FOR EACH ROW EXECUTE FUNCTION activity_owner_cleanup();

-- Once a season is explicitly activated, an old/disabled writer must not silently
-- change ownership without the matching policy source. Deferred to inspect the
-- final state of the complete verdict/ownership/score transaction, including CASCADE.
CREATE OR REPLACE FUNCTION activity_ownership_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_site TEXT; active_id TEXT; current_pet UUID; period_pet UUID;
        current_claim UUID; period_claim UUID; cert TEXT; verified BIGINT;
BEGIN
    SELECT id INTO active_id FROM activity_seasons WHERE status='ACTIVE';
    IF active_id IS NULL THEN RETURN NULL; END IF;
    target_site := COALESCE(NEW.site_id, OLD.site_id);
    SELECT c.pet_id,o.claim_id,o.certification INTO current_pet,current_claim,cert
        FROM territory_occupancies o JOIN territory_claims c ON c.id=o.claim_id
        WHERE o.site_id=target_site;
    SELECT pet_id,claim_id,verified_from_ms INTO period_pet,period_claim,verified
        FROM activity_holding_periods
        WHERE season_id=active_id AND site_id=target_site AND ended_ms IS NULL;
    IF current_pet IS DISTINCT FROM period_pet OR current_claim IS DISTINCT FROM period_claim
       OR (current_pet IS NOT NULL AND (cert='VERIFIED') IS DISTINCT FROM (verified IS NOT NULL))
    THEN RAISE EXCEPTION 'activity ownership source mismatch' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS activity_ownership_guard ON territory_occupancies;
CREATE CONSTRAINT TRIGGER activity_ownership_guard
AFTER INSERT OR UPDATE OR DELETE ON territory_occupancies DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION activity_ownership_guard();
DROP TRIGGER IF EXISTS activity_period_guard ON activity_holding_periods;
CREATE CONSTRAINT TRIGGER activity_period_guard
AFTER INSERT OR UPDATE OR DELETE ON activity_holding_periods DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION activity_ownership_guard();
