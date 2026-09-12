-- Read the catalog and compare with a temporary definition; never mutate application rows.
DO $$
DECLARE target REGCLASS := to_regclass('territory_attempts'); field RECORD; expected RECORD;
BEGIN
    IF target IS NULL THEN RAISE EXCEPTION 'missing table territory_attempts'; END IF;
    CREATE TEMP TABLE IF NOT EXISTS territory_vision_expected (
        id UUID, status VARCHAR(24), photo_redacted_at TIMESTAMPTZ,
        vision_lease_token UUID,
        vision_lease_until TIMESTAMPTZ,
        vision_attempts INTEGER NOT NULL DEFAULT 0,
        vision_available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        vision_dispatch_after TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        vision_retry_reason TEXT,
        CONSTRAINT territory_vision_attempts_check CHECK (vision_attempts >= 0 AND vision_attempts <= 2),
        CONSTRAINT territory_vision_lease_check CHECK (
            (vision_lease_token IS NULL AND vision_lease_until IS NULL)
            OR (vision_lease_token IS NOT NULL AND vision_lease_until IS NOT NULL AND status = 'VISION_PENDING')
        )
    ) ON COMMIT DROP;
    CREATE INDEX IF NOT EXISTS territory_vision_expected_idx
        ON territory_vision_expected (vision_dispatch_after, id)
        WHERE status = 'VISION_PENDING'
           OR (status IN ('VERIFIED','REJECTED','FAILED') AND photo_redacted_at IS NULL);
    FOR field IN
        SELECT a.*, pg_get_expr(d.adbin,d.adrelid) AS def
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid='pg_temp.territory_vision_expected'::regclass AND a.attname LIKE 'vision_%'
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=target AND a.attname=field.attname AND NOT a.attisdropped
              AND a.atttypid=field.atttypid AND a.atttypmod=field.atttypmod
              AND a.attnotnull=field.attnotnull
              AND pg_get_expr(d.adbin,d.adrelid) IS NOT DISTINCT FROM field.def
        ) THEN RAISE EXCEPTION 'territory vision column mismatch %', field.attname; END IF;
    END LOOP;
    FOR expected IN SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint
        WHERE conrelid='pg_temp.territory_vision_expected'::regclass AND contype='c'
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=target
            AND conname=expected.conname AND convalidated AND pg_get_constraintdef(oid)=expected.def)
        THEN RAISE EXCEPTION 'territory vision constraint mismatch %', expected.conname; END IF;
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am am ON am.oid=c.relam
        WHERE i.indrelid=target AND c.relname='territory_vision_dispatch_idx'
          AND i.indisvalid AND i.indisready AND NOT i.indisunique AND am.amname='btree'
          AND i.indnatts=2 AND pg_get_indexdef(i.indexrelid,1,TRUE)='vision_dispatch_after'
          AND pg_get_indexdef(i.indexrelid,2,TRUE)='id'
          AND pg_get_expr(i.indpred,i.indrelid)=(
              SELECT pg_get_expr(indpred,indrelid) FROM pg_index
              WHERE indexrelid='pg_temp.territory_vision_expected_idx'::regclass)
    ) THEN RAISE EXCEPTION 'territory vision dispatch index mismatch'; END IF;
END $$;
