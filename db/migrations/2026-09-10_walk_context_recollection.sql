-- Additive: apply before deploying the new worker. Existing rounds remain zero.
ALTER TABLE walk_entry_context_jobs
    ADD COLUMN IF NOT EXISTS collection_round INTEGER NOT NULL DEFAULT 0 CHECK (collection_round >= 0),
    ADD COLUMN IF NOT EXISTS backfill_policy TEXT;
ALTER TABLE walk_entry_context_envelopes
    ADD COLUMN IF NOT EXISTS collection_round INTEGER NOT NULL DEFAULT 0 CHECK (collection_round >= 0);
ALTER TABLE walk_entry_context_envelopes
    DROP CONSTRAINT IF EXISTS walk_entry_context_envelopes_job_id_attempt_key;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'walk_entry_context_envelopes'::regclass
                     AND conname = 'walk_entry_context_envelopes_round_attempt_key') THEN
        ALTER TABLE walk_entry_context_envelopes ADD CONSTRAINT
            walk_entry_context_envelopes_round_attempt_key UNIQUE (job_id, collection_round, attempt);
    END IF;
END;
$$;
