-- Nullable expiry keeps historical ownership policies unchanged. First-season writers set it.
ALTER TABLE territory_occupancies ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS territory_occupancies_expiry_idx
    ON territory_occupancies(expires_at) WHERE expires_at IS NOT NULL;

-- Exact retries of a GPS renewal must never extend the lease twice.
CREATE TABLE IF NOT EXISTS territory_renewals (
    id UUID PRIMARY KEY,
    claim_id UUID NOT NULL REFERENCES territory_claims(id) ON DELETE CASCADE,
    season_id VARCHAR(128) NOT NULL,
    contact VARCHAR(1024) NOT NULL,
    site_version BIGINT NOT NULL CHECK (site_version >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS territory_renewals_claim_idx ON territory_renewals(claim_id);
