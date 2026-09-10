-- Read-only: verify the five allowed tags, without printing user payloads.
DO $$
DECLARE labels text[];
BEGIN
    IF to_regclass('walk_entry_context_jobs') IS NULL THEN
        RAISE EXCEPTION 'missing table: walk_entry_context_jobs';
    END IF;
    SELECT array_agg(parts[1] ORDER BY parts[1]) INTO labels
      FROM pg_constraint c,
           LATERAL regexp_matches(pg_get_constraintdef(c.oid), '''([^'']+)''', 'g') AS parts
     WHERE c.conrelid = 'walk_entry_context_jobs'::regclass
       AND c.conname = 'walk_entry_context_jobs_tag_check'
       AND c.contype = 'c' AND c.convalidated;
    IF labels IS DISTINCT FROM ARRAY[
        'environment.weather', 'space.address', 'space.facility', 'space.park', 'space.river'
    ]::text[] THEN
        RAISE EXCEPTION 'tag constraint mismatch: walk public context';
    END IF;
END;
$$;
