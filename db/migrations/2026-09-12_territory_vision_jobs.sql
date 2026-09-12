-- Apply before starting the lease-aware web/worker/Beat; stop old photo workers first.
BEGIN;
ALTER TABLE territory_attempts
    ADD COLUMN IF NOT EXISTS vision_lease_token UUID,
    ADD COLUMN IF NOT EXISTS vision_lease_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS vision_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS vision_available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS vision_dispatch_after TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS vision_retry_reason TEXT;
-- Existing VISION_PENDING and unredacted terminal rows become discoverable immediately.
-- Reapplication preserves live leases and retry counters.
ALTER TABLE territory_attempts DROP CONSTRAINT IF EXISTS territory_vision_attempts_check;
ALTER TABLE territory_attempts ADD CONSTRAINT territory_vision_attempts_check
    CHECK (vision_attempts >= 0 AND vision_attempts <= 2);
ALTER TABLE territory_attempts DROP CONSTRAINT IF EXISTS territory_vision_lease_check;
ALTER TABLE territory_attempts ADD CONSTRAINT territory_vision_lease_check CHECK (
    (vision_lease_token IS NULL AND vision_lease_until IS NULL)
    OR (vision_lease_token IS NOT NULL AND vision_lease_until IS NOT NULL AND status = 'VISION_PENDING')
);
CREATE INDEX IF NOT EXISTS territory_vision_dispatch_idx
    ON territory_attempts (vision_dispatch_after, id)
    WHERE status = 'VISION_PENDING'
       OR (status IN ('VERIFIED','REJECTED','FAILED') AND photo_redacted_at IS NULL);
COMMIT;
