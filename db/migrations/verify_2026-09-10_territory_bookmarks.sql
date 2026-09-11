-- Read-only catalog assertions; never writes member data.
DO $$
DECLARE field RECORD; expected RECORD;
BEGIN
    IF to_regclass('territory_bookmarks') IS NULL THEN
        RAISE EXCEPTION 'missing table: territory_bookmarks';
    END IF;
    FOR field IN SELECT * FROM (VALUES
        ('app_user_id', 'uuid'),
        ('site_id', 'character varying(96)'),
        ('created_at', 'timestamp with time zone')
    ) AS fields(col, kind) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_attribute
            WHERE attrelid='territory_bookmarks'::regclass AND attname=field.col
            AND NOT attisdropped AND attnotnull
            AND format_type(atttypid, atttypmod)=field.kind) THEN
            RAISE EXCEPTION 'bookmark column mismatch: %', field.col;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a
        ON a.attrelid=d.adrelid AND a.attnum=d.adnum
        WHERE d.adrelid='territory_bookmarks'::regclass AND a.attname='created_at'
        AND pg_get_expr(d.adbin,d.adrelid)='now()') THEN
        RAISE EXCEPTION 'bookmark created_at default mismatch';
    END IF;
    FOR expected IN SELECT * FROM (VALUES
        ('PRIMARY KEY (app_user_id, site_id)'),
        ('FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE'),
        ($def$CHECK (((site_id)::text ~ '^territory-site:hex-v1:140:-?[0-9]+:-?[0-9]+$'::text))$def$)
    ) AS checks(definition) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='territory_bookmarks'::regclass AND convalidated
            AND NOT condeferrable AND pg_get_constraintdef(oid)=expected.definition) THEN
            RAISE EXCEPTION 'bookmark constraint mismatch: %', expected.definition;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        WHERE i.indrelid='territory_bookmarks'::regclass
        AND c.relname='territory_bookmarks_site_idx'
        AND i.indisvalid AND i.indisready AND i.indpred IS NULL
        AND pg_get_indexdef(i.indexrelid) LIKE '% USING btree (site_id, app_user_id)') THEN
        RAISE EXCEPTION 'bookmark site index mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_attribute a
        ON a.attrelid=t.tgrelid AND a.attname='status'
        WHERE t.tgrelid='app_users'::regclass AND t.tgname='territory_bookmark_owner_cleanup'
        AND NOT t.tgisinternal AND t.tgenabled='O' AND t.tgtype=17 AND t.tgqual IS NULL
        AND t.tgattr::text=a.attnum::text
        AND t.tgfoid=to_regprocedure('territory_bookmark_owner_cleanup()')) THEN
        RAISE EXCEPTION 'bookmark withdrawal trigger mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
        WHERE oid=to_regprocedure('territory_bookmark_owner_cleanup()')
        AND prorettype='trigger'::regtype AND NOT prosecdef
        AND regexp_replace(prosrc, '\s+', '', 'g') =
            regexp_replace($body$
                BEGIN
                    IF NEW.status = 'withdrawn' THEN
                        DELETE FROM territory_bookmarks WHERE app_user_id = NEW.id;
                    END IF;
                    RETURN NEW;
                END
            $body$, '\s+', '', 'g')) THEN
        RAISE EXCEPTION 'bookmark withdrawal function mismatch';
    END IF;
END $$;
