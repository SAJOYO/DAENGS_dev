-- Member-owned stable source records. name is a fallback for unavailable records, not live facts.
CREATE TABLE IF NOT EXISTS place_bookmarks (
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    source VARCHAR(64) NOT NULL,
    ref VARCHAR(256) NOT NULL,
    name VARCHAR(500) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (app_user_id, source, ref),
    CONSTRAINT place_bookmarks_source_check CHECK (source IN
        ('kcisa', 'kto', 'public:mois:animal_hospital', 'public:mois:animal_pharmacy')),
    CONSTRAINT place_bookmarks_ref_check CHECK (length(ref) > 0)
);

CREATE OR REPLACE FUNCTION place_bookmark_owner_cleanup()
RETURNS trigger LANGUAGE plpgsql AS $func$
BEGIN
    IF NEW.status = 'withdrawn' THEN
        DELETE FROM place_bookmarks WHERE app_user_id = NEW.id;
    END IF;
    RETURN NEW;
END $func$;
DROP TRIGGER IF EXISTS place_bookmark_owner_cleanup ON app_users;
CREATE TRIGGER place_bookmark_owner_cleanup AFTER UPDATE OF status ON app_users
FOR EACH ROW EXECUTE FUNCTION place_bookmark_owner_cleanup();
