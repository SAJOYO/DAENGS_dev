-- Shared ownership is distinct from photo visits. Safe to run again on existing DBs.
CREATE TABLE IF NOT EXISTS territory_claim_sessions (
    id UUID PRIMARY KEY,
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    client_session_id UUID NOT NULL,
    pet_ids UUID[] NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    phase VARCHAR(16) NOT NULL CHECK (phase IN ('RECORDING','PAUSED','ENDED')),
    version BIGINT NOT NULL CHECK (version >= 0),
    UNIQUE(app_user_id, client_session_id)
);
CREATE TABLE IF NOT EXISTS territory_claim_sites (
    site_id VARCHAR(96) PRIMARY KEY,
    version BIGINT NOT NULL CHECK (version >= 0)
);
CREATE TABLE IF NOT EXISTS territory_claims (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES territory_claim_sessions(id) ON DELETE CASCADE,
    site_id VARCHAR(96) NOT NULL REFERENCES territory_claim_sites(site_id),
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    expected_site_version BIGINT NOT NULL CHECK (expected_site_version >= 0),
    disposition VARCHAR(24) NOT NULL CHECK (disposition IN ('GRANTED','PHOTO_REQUIRED','POLICY_UNDECIDED','ALREADY_OWNED')),
    photo_status VARCHAR(24) NOT NULL CHECK (photo_status IN ('NOT_SUBMITTED','PENDING','VERIFIED','REJECTED','RETRY_PENDING')),
    current_photo_id UUID REFERENCES territory_attempts(id) ON DELETE SET NULL,
    resolution_code VARCHAR(40),
    contact VARCHAR(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(session_id, site_id)
);
CREATE TABLE IF NOT EXISTS territory_occupancies (
    site_id VARCHAR(96) PRIMARY KEY REFERENCES territory_claim_sites(site_id),
    claim_id UUID NOT NULL UNIQUE REFERENCES territory_claims(id) ON DELETE CASCADE,
    certification VARCHAR(16) NOT NULL CHECK (certification IN ('UNVERIFIED','VERIFIED')),
    occupied_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS territory_claim_photos (
    photo_id UUID PRIMARY KEY REFERENCES territory_attempts(id) ON DELETE CASCADE,
    claim_id UUID NOT NULL REFERENCES territory_claims(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_territory_claim_photos_claim_id ON territory_claim_photos(claim_id);
CREATE INDEX IF NOT EXISTS territory_claims_pet_idx ON territory_claims(pet_id);
