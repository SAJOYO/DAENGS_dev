-- Apply before web/photo workers. No season rules are rewritten.
ALTER TABLE territory_occupancies ADD COLUMN IF NOT EXISTS certified_at TIMESTAMPTZ;
-- Historical certification time was not stored. Preserve its existing protection baseline.
UPDATE territory_occupancies SET certified_at=occupied_at
WHERE certification='VERIFIED' AND certified_at IS NULL;
CREATE TABLE IF NOT EXISTS territory_challenges (
 id UUID PRIMARY KEY,
 claim_id UUID NOT NULL REFERENCES territory_claims(id) ON DELETE CASCADE,
 expected_site_version BIGINT NOT NULL CHECK(expected_site_version >= 0),
 season_id VARCHAR(128) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL CHECK(expires_at > created_at),
 photo_id UUID UNIQUE REFERENCES territory_attempts(id) ON DELETE SET NULL,
 resolution_code VARCHAR(40),
 completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_territory_challenges_claim_id ON territory_challenges(claim_id);

