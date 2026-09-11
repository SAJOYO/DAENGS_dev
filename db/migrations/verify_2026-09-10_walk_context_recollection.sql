-- Verify metadata only; never expose entries or provider payloads.
DO $$
DECLARE
    rel TEXT;
BEGIN
    FOREACH rel IN ARRAY ARRAY['walk_entry_context_jobs', 'walk_entry_context_envelopes'] LOOP
        IF to_regclass(rel) IS NULL THEN
            RAISE EXCEPTION 'missing table: %', rel;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_attribute
                       WHERE attrelid = rel::regclass AND attname = 'collection_round'
                         AND atttypid = 'integer'::regtype AND attnotnull AND NOT attisdropped) THEN
            RAISE EXCEPTION 'mismatch: collection round: %', rel;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_attrdef d
                        ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                       WHERE a.attrelid = rel::regclass AND a.attname = 'collection_round'
                         AND pg_get_expr(d.adbin, d.adrelid) = '0') THEN
            RAISE EXCEPTION 'mismatch: round default: %', rel;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conrelid = rel::regclass AND contype = 'c' AND convalidated
                         AND pg_get_constraintdef(oid) = 'CHECK ((collection_round >= 0))') THEN
            RAISE EXCEPTION 'mismatch: round guard: %', rel;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_attribute
                   WHERE attrelid = 'walk_entry_context_jobs'::regclass
                     AND attname = 'backfill_policy' AND atttypid = 'text'::regtype AND NOT attisdropped)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conrelid = 'walk_entry_context_envelopes'::regclass
                         AND contype = 'u' AND convalidated
                         AND pg_get_constraintdef(oid) = 'UNIQUE (job_id, collection_round, attempt)')
       OR EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'walk_entry_context_envelopes'::regclass
                     AND contype = 'u' AND pg_get_constraintdef(oid) = 'UNIQUE (job_id, attempt)') THEN
        RAISE EXCEPTION 'mismatch: collection history uniqueness/backfill policy';
    END IF;
END;
$$;
