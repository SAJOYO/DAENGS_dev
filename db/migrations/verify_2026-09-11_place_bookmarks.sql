-- Read-only catalog assertions. No member data is changed by verification.
DO $$
DECLARE field RECORD;
BEGIN
    IF to_regclass('place_bookmarks') IS NULL THEN
        RAISE EXCEPTION 'missing table: place_bookmarks';
    END IF;
    FOR field IN SELECT * FROM (VALUES
        ('app_user_id', 'uuid'), ('source', 'character varying(64)'),
        ('ref', 'character varying(256)'), ('name', 'character varying(500)'),
        ('created_at', 'timestamp with time zone')
    ) AS fields(col, kind) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='place_bookmarks'::regclass
            AND attname=field.col AND NOT attisdropped AND attnotnull
            AND format_type(atttypid, atttypmod)=field.kind) THEN
            RAISE EXCEPTION 'bookmark column mismatch: %', field.col;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a
        ON a.attrelid=d.adrelid AND a.attnum=d.adnum
        WHERE d.adrelid='place_bookmarks'::regclass AND a.attname='created_at'
        AND pg_get_expr(d.adbin,d.adrelid)='now()') THEN
        RAISE EXCEPTION 'bookmark timestamp default mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='place_bookmarks'::regclass
        AND convalidated AND NOT condeferrable
        AND pg_get_constraintdef(oid)='PRIMARY KEY (app_user_id, source, ref)') THEN
        RAISE EXCEPTION 'bookmark primary key mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='place_bookmarks'::regclass
        AND convalidated AND NOT condeferrable
        AND pg_get_constraintdef(oid)='FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE') THEN
        RAISE EXCEPTION 'bookmark ownership FK mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='place_bookmarks'::regclass
        AND conname='place_bookmarks_source_check' AND convalidated
        AND pg_get_constraintdef(oid)=$def$CHECK (((source)::text = ANY ((ARRAY['kcisa'::character varying, 'kto'::character varying, 'public:mois:animal_hospital'::character varying, 'public:mois:animal_pharmacy'::character varying])::text[])))$def$) THEN
        RAISE EXCEPTION 'bookmark source constraint mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='place_bookmarks'::regclass
        AND conname='place_bookmarks_ref_check' AND convalidated
        AND pg_get_constraintdef(oid)='CHECK ((length((ref)::text) > 0))') THEN
        RAISE EXCEPTION 'bookmark ref constraint mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_attribute a
        ON a.attrelid=t.tgrelid AND a.attname='status'
        WHERE t.tgrelid='app_users'::regclass AND t.tgname='place_bookmark_owner_cleanup'
        AND t.tgenabled='O' AND t.tgtype=17 AND NOT t.tgisinternal
        AND t.tgattr::text=a.attnum::text
        AND t.tgfoid='place_bookmark_owner_cleanup()'::regprocedure) THEN
        RAISE EXCEPTION 'bookmark withdrawal trigger mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE oid='place_bookmark_owner_cleanup()'::regprocedure
        AND regexp_replace(prosrc, '\s+', ' ', 'g') =
            ' BEGIN IF NEW.status = ''withdrawn'' THEN DELETE FROM place_bookmarks WHERE app_user_id = NEW.id; END IF; RETURN NEW; END ') THEN
        RAISE EXCEPTION 'bookmark cleanup body mismatch';
    END IF;
END $$;
