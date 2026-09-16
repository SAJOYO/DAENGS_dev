-- Immutable detail output. Application publishes parent and all pages in one transaction.
CREATE TABLE IF NOT EXISTS walk_measurements (
    walk_id uuid NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    measurement_id varchar(71) NOT NULL,
    input_key varchar(64) NOT NULL,
    payload text NOT NULL,
    fingerprint varchar(64) NOT NULL,
    PRIMARY KEY (walk_id, measurement_id),
    CONSTRAINT walk_measurement_input UNIQUE (walk_id, input_key),
    CONSTRAINT walk_measurement_id CHECK (measurement_id ~ '^shadow-[0-9a-f]{64}$'),
    CONSTRAINT walk_measurement_input_hash CHECK (input_key ~ '^[0-9a-f]{64}$'),
    CONSTRAINT walk_measurement_hash CHECK (fingerprint ~ '^[0-9a-f]{64}$')
);
CREATE TABLE IF NOT EXISTS walk_measurement_chunks (
    walk_id uuid NOT NULL,
    measurement_id varchar(71) NOT NULL,
    chunk_index integer NOT NULL,
    payload text NOT NULL,
    fingerprint varchar(64) NOT NULL,
    PRIMARY KEY (walk_id, measurement_id, chunk_index),
    FOREIGN KEY (walk_id, measurement_id) REFERENCES walk_measurements(walk_id, measurement_id) ON DELETE CASCADE,
    CONSTRAINT walk_measurement_chunk_index CHECK (chunk_index BETWEEN 0 AND 1999),
    CONSTRAINT walk_measurement_chunk_hash CHECK (fingerprint ~ '^[0-9a-f]{64}$')
);
