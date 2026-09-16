-- Catalog assertions only; no GPS or member data is modified.
DO $$
DECLARE item RECORD;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('walk_precision_backups','walk_id','uuid',true),
        ('walk_precision_backups','manifest','jsonb',true),
        ('walk_precision_backups','manifest_fingerprint','character varying(71)',true),
        ('walk_precision_backups','evidence_fingerprint','character varying(71)',false),
        ('walk_precision_chunks','walk_id','uuid',true),
        ('walk_precision_chunks','chunk_index','integer',true),
        ('walk_precision_chunks','payload','jsonb',true),
        ('walk_precision_chunks','fingerprint','character varying(71)',true)
    ) AS fields(tbl,col,kind,required) LOOP
        IF to_regclass(item.tbl) IS NULL OR NOT EXISTS (
            SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass(item.tbl) AND attname=item.col
            AND NOT attisdropped AND attnotnull=item.required AND format_type(atttypid,atttypmod)=item.kind
        ) THEN RAISE EXCEPTION 'motion column mismatch: %.%', item.tbl,item.col; END IF;
    END LOOP;
    FOR item IN SELECT * FROM (VALUES
        ('walk_precision_backups','walk_precision_backups_pkey','PRIMARY KEY (walk_id)'),
        ('walk_precision_backups','walk_precision_backups_walk_id_fkey','FOREIGN KEY (walk_id) REFERENCES walk_motion_backups(walk_id) ON DELETE CASCADE'),
        ('walk_precision_chunks','walk_precision_chunks_pkey','PRIMARY KEY (walk_id, chunk_index)'),
        ('walk_precision_chunks','walk_precision_chunks_walk_id_fkey','FOREIGN KEY (walk_id) REFERENCES walk_precision_backups(walk_id) ON DELETE CASCADE'),
        ('walk_precision_backups','walk_precision_manifest_object',$d$CHECK ((jsonb_typeof(manifest) = 'object'::text))$d$),
        ('walk_precision_backups','walk_precision_manifest_hash',$d$CHECK (((manifest_fingerprint)::text ~ '^sha256:[0-9a-f]{64}$'::text))$d$),
        ('walk_precision_backups','walk_precision_evidence_hash',$d$CHECK (((evidence_fingerprint)::text ~ '^sha256:[0-9a-f]{64}$'::text))$d$),
        ('walk_precision_chunks','walk_precision_chunk_index','CHECK (((chunk_index >= 0) AND (chunk_index <= 390)))'),
        ('walk_precision_chunks','walk_precision_chunk_payload',$d$CHECK (((jsonb_typeof(payload) = 'array'::text) AND ((jsonb_array_length(payload) >= 1) AND (jsonb_array_length(payload) <= 256))))$d$),
        ('walk_precision_chunks','walk_precision_chunk_hash',$d$CHECK (((fingerprint)::text ~ '^sha256:[0-9a-f]{64}$'::text))$d$)
    ) AS constraints(tbl,key,definition) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(item.tbl)
            AND conname=item.key AND convalidated AND NOT condeferrable
            AND pg_get_constraintdef(oid)=item.definition) THEN
            RAISE EXCEPTION 'motion constraint mismatch: %',item.key;
        END IF;
    END LOOP;
END $$;
