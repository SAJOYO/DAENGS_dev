-- Read-only verification; fails rather than silently accepting an incomplete rollout.
DO $$
DECLARE field RECORD;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'territory_occupancies'
        AND column_name = 'certified_at' AND data_type = 'timestamp with time zone') THEN
        RAISE EXCEPTION 'territory_occupancies.certified_at mismatch';
    END IF;
    IF to_regclass('territory_challenges') IS NULL THEN
        RAISE EXCEPTION 'missing table territory_challenges';
    END IF;
    IF EXISTS (SELECT 1 FROM territory_occupancies WHERE certification = 'VERIFIED' AND certified_at IS NULL) THEN
        RAISE EXCEPTION 'verified occupancy backfill mismatch';
    END IF;
    FOR field IN SELECT * FROM (VALUES
        ('id', 'uuid', 'NO'), ('claim_id', 'uuid', 'NO'),
        ('expected_site_version', 'bigint', 'NO'), ('season_id', 'character varying', 'NO'),
        ('created_at', 'timestamp with time zone', 'NO'), ('expires_at', 'timestamp with time zone', 'NO'),
        ('photo_id', 'uuid', 'YES'), ('resolution_code', 'character varying', 'YES'),
        ('completed_at', 'timestamp with time zone', 'YES')
    ) AS fields(name, kind, nullable) LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema()
            AND table_name='territory_challenges' AND column_name=field.name
            AND data_type=field.kind AND is_nullable=field.nullable) THEN
            RAISE EXCEPTION 'territory_challenges column % mismatch', field.name;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='territory_challenges'::regclass
        AND contype='p' AND pg_get_constraintdef(oid)='PRIMARY KEY (id)')
    OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='territory_challenges'::regclass
        AND contype='u' AND pg_get_constraintdef(oid)='UNIQUE (photo_id)')
    OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='territory_challenges'::regclass
        AND contype='f' AND pg_get_constraintdef(oid)='FOREIGN KEY (claim_id) REFERENCES territory_claims(id) ON DELETE CASCADE')
    OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='territory_challenges'::regclass
        AND contype='f' AND pg_get_constraintdef(oid)='FOREIGN KEY (photo_id) REFERENCES territory_attempts(id) ON DELETE SET NULL')
    OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='territory_challenges'::regclass
        AND contype='c' AND pg_get_constraintdef(oid)='CHECK ((expected_site_version >= 0))')
    OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='territory_challenges'::regclass
        AND contype='c' AND pg_get_constraintdef(oid)='CHECK ((expires_at > created_at))') THEN
        RAISE EXCEPTION 'territory_challenges constraints mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname=current_schema()
        AND tablename='territory_challenges' AND indexname='ix_territory_challenges_claim_id'
        AND indexdef LIKE '% USING btree (claim_id)') THEN
        RAISE EXCEPTION 'territory_challenges claim index mismatch';
    END IF;
END $$;
