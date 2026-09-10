-- Read-only catalog assertions; never output action/pin/receipt payloads.
DO $verify$
DECLARE item record; relation regclass;
BEGIN
    IF to_regclass('walk_entry_pins') IS NULL OR to_regclass('walk_entry_mutations') IS NULL THEN
        RAISE EXCEPTION 'missing table: walk entry v2';
    END IF;
    FOR item IN SELECT * FROM (VALUES
        ('walk_entry_pins', 'walk_id', 'uuid', true),
        ('walk_entry_pins', 'entry_id', 'uuid', true),
        ('walk_entry_pins', 'pin_revision', 'integer', true),
        ('walk_entry_pins', 'payload', 'jsonb', false),
        ('walk_entry_mutations', 'walk_id', 'uuid', true),
        ('walk_entry_mutations', 'entry_id', 'uuid', true),
        ('walk_entry_mutations', 'mutation_id', 'uuid', true),
        ('walk_entry_mutations', 'request_hash', 'character varying(64)', true),
        ('walk_entry_mutations', 'response', 'jsonb', true)
    ) AS expected(table_name, column_name, type_name, required) LOOP
        relation := to_regclass(item.table_name);
        IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = relation
                       AND a.attname = item.column_name AND a.attnum > 0 AND NOT a.attisdropped
                       AND format_type(a.atttypid, a.atttypmod) = item.type_name
                       AND a.attnotnull = item.required) THEN
            RAISE EXCEPTION 'column mismatch: %.%', item.table_name, item.column_name;
        END IF;
    END LOOP;
    FOR item IN SELECT * FROM (VALUES
        ('walk_entry_pins', 'p', 'walk_id,entry_id'),
        ('walk_entry_mutations', 'p', 'walk_id,entry_id,mutation_id'),
        ('walk_entry_pins', 'f', 'walk_id,entry_id'),
        ('walk_entry_mutations', 'f', 'walk_id,entry_id'),
        ('walk_entry_pins', 'c', 'pin_revision'),
        ('walk_entry_pins', 'c', 'payload'),
        ('walk_entry_mutations', 'c', 'request_hash'),
        ('walk_entry_mutations', 'c', 'response')
    ) AS expected(table_name, kind, columns) LOOP
        relation := to_regclass(item.table_name);
        IF NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = relation
            AND c.contype::text = item.kind AND c.convalidated
            AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                      JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                      ORDER BY k.pos) = string_to_array(item.columns, ',')
            AND (item.kind <> 'f' OR (c.confrelid = 'walk_entries'::regclass AND c.confdeltype = 'c'))
            AND (item.kind <> 'p' OR EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid = c.conindid
                                           AND i.indisvalid AND i.indisready AND i.indisunique))) THEN
            RAISE EXCEPTION 'constraint mismatch: % % %', item.table_name, item.kind, item.columns;
        END IF;
    END LOOP;
    FOR item IN SELECT * FROM (VALUES
        ('walk_entries', 'walk_entry_pins_deleted'),
        ('walk_entry_pins', 'walk_entry_pin_live'),
        ('walk_entry_mutations', 'walk_entry_mutation_live')
    ) AS expected(table_name, trigger_name) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass(item.table_name)
                       AND tgname = item.trigger_name AND tgenabled = 'O') THEN
            RAISE EXCEPTION 'trigger mismatch: %.%', item.table_name, item.trigger_name;
        END IF;
    END LOOP;
END;
$verify$;
