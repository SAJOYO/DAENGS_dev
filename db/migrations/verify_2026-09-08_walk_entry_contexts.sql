-- search_path에 선택한 스키마만 검사한다. 원본/봉투 payload는 출력하지 않는다.
DO $$
BEGIN
    IF to_regclass('walk_entry_context_jobs') IS NULL
       OR to_regclass('walk_entry_context_envelopes') IS NULL THEN
        RAISE EXCEPTION 'walk entry context tables missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                   WHERE tgrelid = 'walk_entries'::regclass
                     AND tgname = 'walk_entry_contexts_deleted' AND tgenabled = 'O') THEN
        RAISE EXCEPTION 'walk entry context deletion trigger missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'walk_entry_context_jobs'::regclass
                     AND confrelid = 'walk_entries'::regclass AND contype = 'f'
                     AND confdeltype = 'c' AND convalidated) THEN
        RAISE EXCEPTION 'walk entry context parent cascade missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'walk_entry_context_envelopes'::regclass
                     AND confrelid = 'walk_entry_context_jobs'::regclass AND contype = 'f'
                     AND confdeltype = 'c' AND convalidated) THEN
        RAISE EXCEPTION 'walk entry context envelope cascade missing';
    END IF;
END;
$$;
