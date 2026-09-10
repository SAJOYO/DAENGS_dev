-- Additive sidecar: apply before enabling v2 reads. Do not remove it when disabling new writes.
CREATE TABLE IF NOT EXISTS walk_entry_pins (
    walk_id UUID NOT NULL,
    entry_id UUID NOT NULL,
    pin_revision INTEGER NOT NULL CONSTRAINT walk_entry_pins_revision CHECK (pin_revision >= 0),
    payload JSONB CHECK (payload IS NULL OR jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (walk_id, entry_id),
    FOREIGN KEY (walk_id, entry_id) REFERENCES walk_entries(walk_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS walk_entry_mutations (
    walk_id UUID NOT NULL,
    entry_id UUID NOT NULL,
    mutation_id UUID NOT NULL,
    request_hash VARCHAR(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    response JSONB NOT NULL CHECK (jsonb_typeof(response) = 'object'),
    PRIMARY KEY (walk_id, entry_id, mutation_id),
    FOREIGN KEY (walk_id, entry_id) REFERENCES walk_entries(walk_id, id) ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION purge_deleted_walk_entry_pins() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Support both older SQL NULL and SQLAlchemy's JSON null tombstones.
    IF NEW.payload IS NULL OR NEW.payload = 'null'::jsonb THEN
        UPDATE walk_entry_pins SET payload = NULL, pin_revision = 0
            WHERE walk_id = NEW.walk_id AND entry_id = NEW.id;
        DELETE FROM walk_entry_mutations WHERE walk_id = NEW.walk_id AND entry_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS walk_entry_pins_deleted ON walk_entries;
CREATE TRIGGER walk_entry_pins_deleted AFTER UPDATE OF payload ON walk_entries
    FOR EACH ROW EXECUTE FUNCTION purge_deleted_walk_entry_pins();

-- Do not allow an old worker/SQL writer to reintroduce coordinates or ACK content after deletion.
CREATE OR REPLACE FUNCTION guard_live_walk_entry_sidecar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE content JSONB;
BEGIN
    SELECT payload INTO content FROM walk_entries
        WHERE walk_id = NEW.walk_id AND id = NEW.entry_id FOR UPDATE;
    IF content IS NULL OR content = 'null'::jsonb THEN
        IF TG_TABLE_NAME = 'walk_entry_mutations' THEN
            RAISE EXCEPTION 'deleted entry cannot retain mutation receipts' USING ERRCODE = '23514';
        ELSIF NEW.payload IS NOT NULL THEN
            RAISE EXCEPTION 'deleted entry cannot retain pin coordinates' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS walk_entry_pin_live ON walk_entry_pins;
CREATE TRIGGER walk_entry_pin_live BEFORE INSERT OR UPDATE ON walk_entry_pins
    FOR EACH ROW EXECUTE FUNCTION guard_live_walk_entry_sidecar();
DROP TRIGGER IF EXISTS walk_entry_mutation_live ON walk_entry_mutations;
CREATE TRIGGER walk_entry_mutation_live BEFORE INSERT OR UPDATE ON walk_entry_mutations
    FOR EACH ROW EXECUTE FUNCTION guard_live_walk_entry_sidecar();
