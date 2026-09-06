-- 수정 가능한 행동/메모. payload NULL은 최소 삭제 표식이며 재생성을 막는다.
CREATE TABLE IF NOT EXISTS walk_entries (
    walk_id UUID NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
    id UUID NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    mutation_id UUID NOT NULL,
    payload JSONB,
    PRIMARY KEY (walk_id, id)
);

