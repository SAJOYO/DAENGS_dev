-- Read-only: historical null expiries remain valid; never backfill policy in SQL.
DO $$
DECLARE field RECORD; expected RECORD;
BEGIN
    IF to_regclass('territory_renewals') IS NULL THEN
        RAISE EXCEPTION 'missing table territory_renewals';
    END IF;
    FOR field IN SELECT * FROM (VALUES
        ('territory_occupancies','expires_at','timestamp with time zone','YES'),
        ('territory_renewals','id','uuid','NO'),
        ('territory_renewals','claim_id','uuid','NO'),
        ('territory_renewals','season_id','character varying','NO'),
        ('territory_renewals','contact','character varying','NO'),
        ('territory_renewals','site_version','bigint','NO'),
        ('territory_renewals','created_at','timestamp with time zone','NO'),
        ('territory_renewals','expires_at','timestamp with time zone','NO')
    ) AS fields(tbl,col,kind,nullable) LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=field.tbl AND column_name=field.col
            AND data_type=field.kind AND is_nullable=field.nullable
            AND (field.col <> 'season_id' OR character_maximum_length=128)
            AND (field.col <> 'contact' OR character_maximum_length=1024)) THEN
            RAISE EXCEPTION 'territory expiry column mismatch %.%',field.tbl,field.col;
        END IF;
    END LOOP;
    FOR expected IN SELECT * FROM (VALUES
        ('PRIMARY KEY (id)'),
        ('FOREIGN KEY (claim_id) REFERENCES territory_claims(id) ON DELETE CASCADE'),
        ('CHECK ((site_version >= 0))'),
        ('CHECK ((expires_at > created_at))')
    ) AS checks(definition) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='territory_renewals'::regclass
            AND convalidated AND pg_get_constraintdef(oid)=expected.definition) THEN
            RAISE EXCEPTION 'territory renewal constraint mismatch %',expected.definition;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname=current_schema()
        AND indexname='territory_occupancies_expiry_idx'
        AND indexdef LIKE '% USING btree (expires_at) WHERE (expires_at IS NOT NULL)')
    OR NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname=current_schema()
        AND indexname='territory_renewals_claim_idx' AND indexdef LIKE '% USING btree (claim_id)') THEN
        RAISE EXCEPTION 'territory expiry index mismatch';
    END IF;
END $$;
