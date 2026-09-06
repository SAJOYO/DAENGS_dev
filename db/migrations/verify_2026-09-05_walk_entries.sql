-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
DO $verify$
DECLARE
    item record;
    relation regclass;
BEGIN
    IF to_regclass('walk_entries') IS NULL THEN
        RAISE EXCEPTION 'missing table: walk_entries';
    END IF;
    FOR item IN SELECT * FROM (VALUES
        ('walk_entries', 'walk_id', 'uuid', 'true'),
        ('walk_entries', 'id', 'uuid', 'true'),
        ('walk_entries', 'revision', 'integer', 'true'),
        ('walk_entries', 'mutation_id', 'uuid', 'true'),
        ('walk_entries', 'payload', 'jsonb', 'false')
    ) AS expected(table_name, column_name, type_name, required) LOOP
        relation := to_regclass(item.table_name);
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: %.% (type %, not null %)',
                item.table_name, item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;
    FOR item IN SELECT * FROM (VALUES
        ('walk_entries', 'p', 'walk_id,id', NULL, NULL, NULL),
        ('walk_entries', 'f', 'walk_id', 'walks', 'id', 'c'),
        ('walk_entries', 'c', 'revision', NULL, NULL, NULL)
    ) AS expected(table_name, kind, columns, target_table, target_columns, delete_action) LOOP
        relation := to_regclass(item.table_name);
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND (item.kind <> 'f' OR (
                  c.confrelid = to_regclass(item.target_table)
                  AND c.confdeltype::text = item.delete_action
                  AND ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num, pos)
                            JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num
                            ORDER BY k.pos) = string_to_array(item.target_columns, ',')
              ))
              AND (item.kind NOT IN ('p', 'u') OR EXISTS (
                  SELECT 1 FROM pg_index i WHERE i.indexrelid = c.conindid
                    AND i.indisvalid AND i.indisready AND i.indisunique
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % kind % columns %',
                item.table_name, item.kind, item.columns;
        END IF;
    END LOOP;
END
$verify$;
