BEGIN;
ALTER TABLE walk_entry_context_jobs
    DROP CONSTRAINT IF EXISTS walk_entry_context_jobs_tag_check;
ALTER TABLE walk_entry_context_jobs
    ADD CONSTRAINT walk_entry_context_jobs_tag_check
    CHECK (tag IN ('space.facility', 'space.park', 'space.river', 'environment.weather', 'space.address', 'space.commerce'));
COMMIT;
