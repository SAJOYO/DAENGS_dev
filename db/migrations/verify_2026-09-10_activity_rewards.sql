-- Read-only verification: fail on missing schema or inconsistent reward balances.
DO $$
DECLARE field RECORD; expected RECORD;
BEGIN
    IF to_regclass('activity_base_rewards') IS NULL OR to_regclass('activity_reward_details') IS NULL THEN
        RAISE EXCEPTION 'missing table activity rewards';
    END IF;
    FOR field IN SELECT * FROM (VALUES
        ('activity_base_rewards','season_id','text'),
        ('activity_base_rewards','app_user_id','uuid'),
        ('activity_base_rewards','site_id','character varying'),
        ('activity_base_rewards','paid','bigint'),
        ('activity_reward_details','season_id','text'),
        ('activity_reward_details','event_id','text'),
        ('activity_reward_details','app_user_id','uuid'),
        ('activity_reward_details','site_id','character varying'),
        ('activity_reward_details','reward_version','text'),
        ('activity_reward_details','base_before','bigint'),
        ('activity_reward_details','base_after','bigint'),
        ('activity_reward_details','base_points','bigint'),
        ('activity_reward_details','takeover_points','bigint')
    ) AS fields(tbl,col,kind) LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=field.tbl AND column_name=field.col
            AND data_type=field.kind AND is_nullable='NO'
            AND (field.col <> 'site_id' OR character_maximum_length=96)) THEN
            RAISE EXCEPTION 'activity reward column mismatch %.%',field.tbl,field.col;
        END IF;
    END LOOP;
    FOR expected IN SELECT * FROM (VALUES
        ('activity_base_rewards','PRIMARY KEY (season_id, app_user_id, site_id)'),
        ('activity_base_rewards','FOREIGN KEY (season_id) REFERENCES activity_seasons(id) ON DELETE CASCADE'),
        ('activity_base_rewards','FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE'),
        ('activity_base_rewards','FOREIGN KEY (site_id) REFERENCES territory_claim_sites(site_id)'),
        ('activity_reward_details','PRIMARY KEY (season_id, event_id)'),
        ('activity_reward_details','FOREIGN KEY (season_id, event_id) REFERENCES activity_game_receipts(season_id, event_id) ON DELETE CASCADE'),
        ('activity_reward_details','FOREIGN KEY (season_id, app_user_id, site_id) REFERENCES activity_base_rewards(season_id, app_user_id, site_id) ON DELETE CASCADE'),
        ('activity_reward_details','CHECK ((base_after = (base_before + base_points)))'),
        ('activity_reward_details','CHECK ((base_points >= 0))'),
        ('activity_reward_details','CHECK ((reward_version = ''first-season-rewards-v1''::text))')
    ) AS checks(tbl,definition) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(expected.tbl)
            AND convalidated AND pg_get_constraintdef(oid)=expected.definition) THEN
            RAISE EXCEPTION 'activity reward constraint mismatch %',expected.definition;
        END IF;
    END LOOP;
    FOR expected IN SELECT * FROM (VALUES
        ('activity_base_rewards','CHECKpaid=ANYARRAY[0,20,100]'),
        ('activity_reward_details','CHECKbase_before=ANYARRAY[0,20,100]'),
        ('activity_reward_details','CHECKbase_after=ANYARRAY[0,20,100]'),
        ('activity_reward_details','CHECKtakeover_points=ANYARRAY[0,20]')
    ) AS checks(tbl,definition) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(expected.tbl)
            AND convalidated AND regexp_replace(pg_get_constraintdef(oid), '\s|::bigint|\(|\)', '', 'g')=expected.definition) THEN
            RAISE EXCEPTION 'activity reward balance constraint mismatch %',expected.definition;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname=current_schema()
        AND indexname='activity_base_rewards_member' AND indexdef LIKE '% USING btree (app_user_id)')
    OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='app_users'::regclass
        AND tgname='activity_reward_owner_cleanup' AND tgenabled='O'
        AND tgfoid='activity_reward_owner_cleanup()'::regprocedure) THEN
        RAISE EXCEPTION 'activity reward cleanup/index mismatch';
    END IF;
    IF EXISTS (SELECT 1 FROM activity_base_rewards WHERE paid NOT IN (0,20,100))
    OR EXISTS (SELECT 1 FROM activity_reward_details d
        JOIN activity_game_receipts r USING (season_id,event_id)
        WHERE d.base_after<>d.base_before+d.base_points OR r.bonus<>d.base_points+d.takeover_points) THEN
        RAISE EXCEPTION 'activity reward stored balance mismatch';
    END IF;
END $$;
