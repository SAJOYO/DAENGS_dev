-- Apply before enabling DAENGS_WALK_PHOTO_METADATA_ENABLED.
CREATE TABLE IF NOT EXISTS walk_photo_manifests (
    walk_id UUID PRIMARY KEY REFERENCES walks(id) ON DELETE CASCADE,
    publisher_id UUID NOT NULL,
    revision INTEGER NOT NULL CONSTRAINT walk_photo_revision_positive CHECK (revision > 0),
    request_hash VARCHAR(64) NOT NULL CONSTRAINT walk_photo_hash_valid CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    records JSONB NOT NULL CONSTRAINT walk_photo_records_bounded
        CHECK (jsonb_typeof(records) = 'array' AND jsonb_array_length(records) <= 200),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
