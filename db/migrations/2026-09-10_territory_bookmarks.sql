-- Apply before deploying the bookmark API. Safe to replay on existing volumes.
BEGIN;
-- Saved neutral sites belong to a member, independently of dogs, claims and seasons.
CREATE TABLE IF NOT EXISTS territory_bookmarks (
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    site_id VARCHAR(96) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (app_user_id, site_id),
    CONSTRAINT territory_bookmarks_site_id_check
        CHECK (site_id ~ '^territory-site:hex-v1:140:-?[0-9]+:-?[0-9]+$')
);
-- Site-first lookup also supports the subsequent owner-change subscription work.
CREATE INDEX IF NOT EXISTS territory_bookmarks_site_idx
    ON territory_bookmarks (site_id, app_user_id);

-- app_users survives withdrawal; ON DELETE CASCADE alone does not remove bookmarks.
CREATE OR REPLACE FUNCTION territory_bookmark_owner_cleanup()
RETURNS trigger LANGUAGE plpgsql AS $func$
BEGIN
    IF NEW.status = 'withdrawn' THEN
        DELETE FROM territory_bookmarks WHERE app_user_id = NEW.id;
    END IF;
    RETURN NEW;
END $func$;
DROP TRIGGER IF EXISTS territory_bookmark_owner_cleanup ON app_users;
CREATE TRIGGER territory_bookmark_owner_cleanup AFTER UPDATE OF status ON app_users
FOR EACH ROW EXECUTE FUNCTION territory_bookmark_owner_cleanup();
COMMIT;
