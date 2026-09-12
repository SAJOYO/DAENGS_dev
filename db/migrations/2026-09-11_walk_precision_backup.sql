-- Original coordinate bits supplement a completed motion backup without rewriting raw points.
CREATE TABLE IF NOT EXISTS walk_precision_backups (
    walk_id uuid PRIMARY KEY REFERENCES walk_motion_backups(walk_id) ON DELETE CASCADE,
    manifest jsonb NOT NULL CONSTRAINT walk_precision_manifest_object CHECK (jsonb_typeof(manifest) = 'object'),
    manifest_fingerprint varchar(71) NOT NULL CONSTRAINT walk_precision_manifest_hash CHECK (manifest_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    evidence_fingerprint varchar(71) CONSTRAINT walk_precision_evidence_hash CHECK (evidence_fingerprint ~ '^sha256:[0-9a-f]{64}$')
);
CREATE TABLE IF NOT EXISTS walk_precision_chunks (
    walk_id uuid REFERENCES walk_precision_backups(walk_id) ON DELETE CASCADE,
    chunk_index integer NOT NULL CONSTRAINT walk_precision_chunk_index CHECK (chunk_index BETWEEN 0 AND 390),
    payload jsonb NOT NULL CONSTRAINT walk_precision_chunk_payload CHECK (jsonb_typeof(payload) = 'array' AND jsonb_array_length(payload) BETWEEN 1 AND 256),
    fingerprint varchar(71) NOT NULL CONSTRAINT walk_precision_chunk_hash CHECK (fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    PRIMARY KEY (walk_id, chunk_index)
);
