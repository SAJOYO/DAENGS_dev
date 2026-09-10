-- Read-only verification. No historical season/rank backfill.
DO $$
DECLARE field RECORD; expected RECORD;
BEGIN
    IF to_regclass('activity_monthly_seasons') IS NULL THEN
        RAISE EXCEPTION 'missing table activity_monthly_seasons';
    END IF;
    FOR field IN SELECT * FROM (VALUES
        ('activity_monthly_seasons','season_id','text','NO'),
        ('activity_monthly_seasons','previous_season_id','text','YES'),
        ('activity_accounts','final_rank','bigint','YES')
    ) AS fields(tbl,col,kind,nullable) LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=field.tbl AND column_name=field.col
            AND data_type=field.kind AND is_nullable=field.nullable) THEN
            RAISE EXCEPTION 'monthly season column mismatch %.%',field.tbl,field.col;
        END IF;
    END LOOP;
    FOR expected IN SELECT * FROM (VALUES
        ('activity_monthly_seasons','PRIMARY KEY (season_id)'),
        ('activity_monthly_seasons','UNIQUE (previous_season_id)'),
        ('activity_monthly_seasons','FOREIGN KEY (season_id) REFERENCES activity_seasons(id) ON DELETE CASCADE'),
        ('activity_monthly_seasons','FOREIGN KEY (previous_season_id) REFERENCES activity_seasons(id)'),
        ('activity_monthly_seasons','CHECK (((previous_season_id IS NULL) OR (previous_season_id <> season_id)))'),
        ('activity_accounts','CHECK ((final_rank > 0))')
    ) AS checks(tbl,definition) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(expected.tbl)
            AND convalidated AND pg_get_constraintdef(oid)=expected.definition) THEN
            RAISE EXCEPTION 'monthly season constraint mismatch %',expected.definition;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM activity_accounts WHERE final_rank IS NOT NULL AND final_score IS NULL)
    OR EXISTS (SELECT 1 FROM activity_monthly_seasons m
        JOIN activity_seasons current_season ON current_season.id=m.season_id
        JOIN activity_seasons previous_season ON previous_season.id=m.previous_season_id
        WHERE previous_season.status<>'FINALIZED' OR previous_season.ends_ms<>current_season.starts_ms) THEN
        RAISE EXCEPTION 'monthly season stored state mismatch';
    END IF;
END $$;
