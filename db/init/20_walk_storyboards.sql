-- Source analysis only. User prose/review snapshots remain independent in the app.
CREATE TABLE IF NOT EXISTS walk_storyboards (
    walk_id UUID PRIMARY KEY REFERENCES walks(id) ON DELETE CASCADE,
    generation BIGINT NOT NULL CONSTRAINT walk_storyboards_generation_check CHECK (generation > 0),
    input_revision VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL CONSTRAINT walk_storyboards_status_check CHECK (status IN ('running','ready','failed')),
    updated_at TIMESTAMPTZ NOT NULL,
    bundle JSONB,
    error_code VARCHAR(80)
);
