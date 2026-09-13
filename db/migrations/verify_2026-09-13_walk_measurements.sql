-- Catalog assertions only; no GPS or member data is modified.
DO $$
DECLARE item RECORD;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('walk_measurement_chunks','walk_id','uuid',true),
        ('walk_measurement_chunks','measurement_id','character varying(71)',true),
        ('walk_measurement_chunks','chunk_index','integer',true),
        ('walk_measurement_chunks','payload','text',true),
        ('walk_measurement_chunks','fingerprint','character varying(64)',true),
        ('walk_measurements','walk_id','uuid',true),
        ('walk_measurements','measurement_id','character varying(71)',true),
        ('walk_measurements','input_key','character varying(64)',true),
        ('walk_measurements','payload','text',true),
        ('walk_measurements','fingerprint','character varying(64)',true)
    ) AS fields(tbl,col,kind,required) LOOP
        IF to_regclass(item.tbl) IS NULL OR NOT EXISTS (
            SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass(item.tbl) AND attname=item.col
            AND NOT attisdropped AND attnotnull=item.required AND format_type(atttypid,atttypmod)=item.kind
        ) THEN RAISE EXCEPTION 'measurement column mismatch: %.%', item.tbl,item.col; END IF;
    END LOOP;
    FOR item IN SELECT * FROM (VALUES
        ('walk_measurement_chunks','walk_measurement_chunk_hash',$d$CHECK (((fingerprint)::text ~ '^[0-9a-f]{64}$'::text))$d$),
        ('walk_measurement_chunks','walk_measurement_chunk_index',$d$CHECK (((chunk_index >= 0) AND (chunk_index <= 1999)))$d$),
        ('walk_measurement_chunks','walk_measurement_chunks_pkey',$d$PRIMARY KEY (walk_id, measurement_id, chunk_index)$d$),
        ('walk_measurement_chunks','walk_measurement_chunks_walk_id_measurement_id_fkey',$d$FOREIGN KEY (walk_id, measurement_id) REFERENCES walk_measurements(walk_id, measurement_id) ON DELETE CASCADE$d$),
        ('walk_measurements','walk_measurement_hash',$d$CHECK (((fingerprint)::text ~ '^[0-9a-f]{64}$'::text))$d$),
        ('walk_measurements','walk_measurement_id',$d$CHECK (((measurement_id)::text ~ '^shadow-[0-9a-f]{64}$'::text))$d$),
        ('walk_measurements','walk_measurement_input',$d$UNIQUE (walk_id, input_key)$d$),
        ('walk_measurements','walk_measurement_input_hash',$d$CHECK (((input_key)::text ~ '^[0-9a-f]{64}$'::text))$d$),
        ('walk_measurements','walk_measurements_pkey',$d$PRIMARY KEY (walk_id, measurement_id)$d$),
        ('walk_measurements','walk_measurements_walk_id_fkey',$d$FOREIGN KEY (walk_id) REFERENCES walks(id) ON DELETE CASCADE$d$)
    ) AS constraints(tbl,key,definition) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(item.tbl)
            AND conname=item.key AND convalidated AND NOT condeferrable
            AND pg_get_constraintdef(oid)=item.definition) THEN
            RAISE EXCEPTION 'measurement constraint mismatch: %',item.key;
        END IF;
    END LOOP;
END $$;
