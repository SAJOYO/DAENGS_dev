-- Read-only catalog checks. Never select private photo metadata.
DO $verify$
DECLARE item record; relation regclass := to_regclass('walk_photo_manifests');
BEGIN
    IF relation IS NULL THEN RAISE EXCEPTION 'missing table: walk_photo_manifests'; END IF;
    FOR item IN SELECT * FROM (VALUES
        ('walk_id', 'uuid'), ('publisher_id', 'uuid'), ('revision', 'integer'),
        ('request_hash', 'character varying(64)'), ('records', 'jsonb'),
        ('updated_at', 'timestamp with time zone')
    ) AS expected(column_name, type_name) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = relation
            AND a.attname = item.column_name AND a.attnum > 0 AND NOT a.attisdropped
            AND a.attnotnull AND format_type(a.atttypid, a.atttypmod) = item.type_name) THEN
            RAISE EXCEPTION 'column mismatch: %', item.column_name;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = relation
        AND c.contype = 'p' AND pg_get_constraintdef(c.oid) = 'PRIMARY KEY (walk_id)') THEN
        RAISE EXCEPTION 'primary key mismatch';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = relation
        AND c.contype = 'f' AND c.confrelid = 'walks'::regclass AND c.confdeltype = 'c'
        AND c.convalidated AND pg_get_constraintdef(c.oid) LIKE 'FOREIGN KEY (walk_id) REFERENCES walks(id)%') THEN
        RAISE EXCEPTION 'walk cascade mismatch';
    END IF;
    FOR item IN SELECT * FROM (VALUES
        ('walk_photo_revision_positive', 'revision > 0'),
        ('walk_photo_hash_valid', '^[0-9a-f]{64}$'),
        ('walk_photo_records_bounded', 'jsonb_array_length(records) <= 200')
    ) AS expected(constraint_name, expression) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = relation AND c.contype = 'c'
            AND c.conname = item.constraint_name AND c.convalidated
            AND position(item.expression IN pg_get_constraintdef(c.oid)) > 0) THEN
            RAISE EXCEPTION 'constraint mismatch: %', item.constraint_name;
        END IF;
    END LOOP;
END;
$verify$;
