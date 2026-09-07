-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
DO $verify$
DECLARE
    item record;
    relation regclass;
BEGIN
    IF to_regclass('territory_claim_sessions') IS NULL THEN
        RAISE EXCEPTION 'missing table: territory_claim_sessions';
    END IF;
    IF to_regclass('territory_claim_sites') IS NULL THEN
        RAISE EXCEPTION 'missing table: territory_claim_sites';
    END IF;
    IF to_regclass('territory_claims') IS NULL THEN
        RAISE EXCEPTION 'missing table: territory_claims';
    END IF;
    IF to_regclass('territory_occupancies') IS NULL THEN
        RAISE EXCEPTION 'missing table: territory_occupancies';
    END IF;
    IF to_regclass('territory_claim_photos') IS NULL THEN
        RAISE EXCEPTION 'missing table: territory_claim_photos';
    END IF;
    FOR item IN SELECT * FROM (VALUES
        ('territory_claim_sessions', 'id', 'uuid', 'true'),
        ('territory_claim_sessions', 'app_user_id', 'uuid', 'true'),
        ('territory_claim_sessions', 'client_session_id', 'uuid', 'true'),
        ('territory_claim_sessions', 'pet_ids', 'uuid[]', 'true'),
        ('territory_claim_sessions', 'started_at', 'timestamp with time zone', 'true'),
        ('territory_claim_sessions', 'phase', 'character varying(16)', 'true'),
        ('territory_claim_sessions', 'version', 'bigint', 'true'),
        ('territory_claim_sites', 'site_id', 'character varying(96)', 'true'),
        ('territory_claim_sites', 'version', 'bigint', 'true'),
        ('territory_claims', 'id', 'uuid', 'true'),
        ('territory_claims', 'session_id', 'uuid', 'true'),
        ('territory_claims', 'site_id', 'character varying(96)', 'true'),
        ('territory_claims', 'pet_id', 'uuid', 'true'),
        ('territory_claims', 'expected_site_version', 'bigint', 'true'),
        ('territory_claims', 'disposition', 'character varying(24)', 'true'),
        ('territory_claims', 'photo_status', 'character varying(24)', 'true'),
        ('territory_claims', 'current_photo_id', 'uuid', 'false'),
        ('territory_claims', 'resolution_code', 'character varying(40)', 'false'),
        ('territory_claims', 'contact', 'character varying(1024)', 'true'),
        ('territory_claims', 'created_at', 'timestamp with time zone', 'true'),
        ('territory_occupancies', 'site_id', 'character varying(96)', 'true'),
        ('territory_occupancies', 'claim_id', 'uuid', 'true'),
        ('territory_occupancies', 'certification', 'character varying(16)', 'true'),
        ('territory_occupancies', 'occupied_at', 'timestamp with time zone', 'true'),
        ('territory_claim_photos', 'photo_id', 'uuid', 'true'),
        ('territory_claim_photos', 'claim_id', 'uuid', 'true')
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
        ('territory_claim_sessions', 'p', 'id', NULL, NULL, NULL),
        ('territory_claim_sites', 'p', 'site_id', NULL, NULL, NULL),
        ('territory_claims', 'p', 'id', NULL, NULL, NULL),
        ('territory_occupancies', 'p', 'site_id', NULL, NULL, NULL),
        ('territory_claim_photos', 'p', 'photo_id', NULL, NULL, NULL),
        ('territory_claim_sessions', 'u', 'app_user_id,client_session_id', NULL, NULL, NULL),
        ('territory_claims', 'u', 'session_id,site_id', NULL, NULL, NULL),
        ('territory_occupancies', 'u', 'claim_id', NULL, NULL, NULL),
        ('territory_claim_sessions', 'c', 'phase', NULL, NULL, NULL),
        ('territory_claim_sessions', 'c', 'version', NULL, NULL, NULL),
        ('territory_claim_sites', 'c', 'version', NULL, NULL, NULL),
        ('territory_claims', 'c', 'expected_site_version', NULL, NULL, NULL),
        ('territory_claims', 'c', 'disposition', NULL, NULL, NULL),
        ('territory_claims', 'c', 'photo_status', NULL, NULL, NULL),
        ('territory_occupancies', 'c', 'certification', NULL, NULL, NULL),
        ('territory_claim_sessions', 'f', 'app_user_id', 'app_users', 'id', 'c'),
        ('territory_claims', 'f', 'session_id', 'territory_claim_sessions', 'id', 'c'),
        ('territory_claims', 'f', 'site_id', 'territory_claim_sites', 'site_id', 'a'),
        ('territory_claims', 'f', 'pet_id', 'pets', 'id', 'c'),
        ('territory_claims', 'f', 'current_photo_id', 'territory_attempts', 'id', 'n'),
        ('territory_occupancies', 'f', 'site_id', 'territory_claim_sites', 'site_id', 'a'),
        ('territory_occupancies', 'f', 'claim_id', 'territory_claims', 'id', 'c'),
        ('territory_claim_photos', 'f', 'photo_id', 'territory_attempts', 'id', 'c'),
        ('territory_claim_photos', 'f', 'claim_id', 'territory_claims', 'id', 'c')
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
    FOR item IN SELECT * FROM (VALUES
        ('territory_claim_photos', 'ix_territory_claim_photos_claim_id', 'claim_id'),
        ('territory_claims', 'territory_claims_pet_idx', 'pet_id')
    ) AS expected(table_name, index_name, columns) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_index i JOIN pg_class idx ON idx.oid = i.indexrelid
            JOIN pg_am am ON am.oid = idx.relam
            WHERE i.indrelid = to_regclass(item.table_name) AND idx.relname = item.index_name
              AND i.indisvalid AND i.indisready AND i.indpred IS NULL
              AND i.indexprs IS NULL AND am.amname = 'btree'
              AND ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
        ) THEN
            RAISE EXCEPTION 'index mismatch: %.%', item.table_name, item.index_name;
        END IF;
    END LOOP;
END
$verify$;
