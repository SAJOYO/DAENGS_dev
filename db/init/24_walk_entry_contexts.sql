-- 원본과 같은 TX에서 예약한다. 외부 조회는 lease를 획득한 워커의 TX 밖에서 수행한다.
CREATE TABLE IF NOT EXISTS walk_entry_context_jobs (
    id UUID PRIMARY KEY,
    walk_id UUID NOT NULL,
    entry_id UUID NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    policy_version TEXT NOT NULL,
    tag TEXT NOT NULL CHECK (tag IN ('space.facility', 'space.park', 'space.river', 'environment.weather')),
    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    FOREIGN KEY (walk_id, entry_id) REFERENCES walk_entries(walk_id, id) ON DELETE CASCADE,
    UNIQUE (walk_id, entry_id, revision, policy_version, tag),
    CHECK ((state = 'running') = (lease_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS walk_entry_context_jobs_due
    ON walk_entry_context_jobs (available_at, id) WHERE state IN ('pending', 'running');

CREATE TABLE IF NOT EXISTS walk_entry_context_envelopes (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES walk_entry_context_jobs(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 3),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    envelope JSONB NOT NULL,
    UNIQUE (job_id, attempt)
);

-- 기능 flag를 끈 뒤 삭제하더라도 수집 위치/응답을 남기지 않는다.
CREATE OR REPLACE FUNCTION purge_deleted_walk_entry_contexts() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload IS NULL THEN
        DELETE FROM walk_entry_context_jobs WHERE walk_id = NEW.walk_id AND entry_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS walk_entry_contexts_deleted ON walk_entries;
CREATE TRIGGER walk_entry_contexts_deleted AFTER UPDATE OF payload ON walk_entries
    FOR EACH ROW EXECUTE FUNCTION purge_deleted_walk_entry_contexts();
