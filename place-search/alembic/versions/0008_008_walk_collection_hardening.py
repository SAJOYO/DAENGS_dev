"""008_walk_collection_hardening.sql

원본 `migrations/008_walk_collection_hardening.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "008_walk_collection_hardening.sql"

SQL = """
-- PR36 후속 하드닝.
-- 1) client_seq는 모바일 재전송의 안정 키, seq는 서버 수신 순서다.
-- 2) finish는 OPEN → SEALED → DERIVED → PURGED 뒤에만 원좌표를 지운다.
-- 3) mock/device 출처와 계산 버전을 결과에 남겨 baseline 오염을 막는다.
-- 4) 원좌표 삭제 전에 공간 좌표가 있는 정지 occurrence를 파생한다.

ALTER TABLE walk_session
    ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'open',
    ADD COLUMN IF NOT EXISTS evidence_origin TEXT NOT NULL DEFAULT 'unknown';

UPDATE walk_session
SET state = CASE WHEN ended_at IS NULL THEN 'open' ELSE 'purged' END,
    evidence_origin = CASE
        WHEN fix_count = 0 THEN 'unknown'
        WHEN mock_fix_count = 0 THEN 'device'
        WHEN mock_fix_count = fix_count THEN 'mock'
        ELSE 'mixed'
    END;

DO $$ BEGIN
    ALTER TABLE walk_session ADD CONSTRAINT walk_session_state_check
        CHECK (state IN ('open', 'sealed', 'derived', 'purged'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE walk_session ADD CONSTRAINT walk_session_origin_check
        CHECK (evidence_origin IN ('device', 'mock', 'mixed', 'unknown'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE walk_fix ADD COLUMN IF NOT EXISTS client_seq INTEGER;
UPDATE walk_fix SET client_seq = seq WHERE client_seq IS NULL;
ALTER TABLE walk_fix ALTER COLUMN client_seq SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS walk_fix_client_seq_uidx
    ON walk_fix (session_id, client_seq);

ALTER TABLE walk_facts
    ADD COLUMN IF NOT EXISTS calculation_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS evidence_origin TEXT NOT NULL DEFAULT 'unknown';

UPDATE walk_facts f
SET record_version = 2,
    evidence_origin = s.evidence_origin
FROM walk_session s
WHERE s.id = f.session_id;

DO $$ BEGIN
    ALTER TABLE walk_facts ADD CONSTRAINT walk_facts_origin_check
        CHECK (evidence_origin IN ('device', 'mock', 'mixed', 'unknown'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS walk_motion_event (
    session_id       TEXT NOT NULL REFERENCES walk_session (id) ON DELETE CASCADE,
    event_index      INTEGER NOT NULL,
    type             TEXT NOT NULL CHECK (type IN ('stop')),
    started_at       TIMESTAMPTZ NOT NULL,
    ended_at         TIMESTAMPTZ NOT NULL,
    duration_s       INTEGER NOT NULL,
    location         GEOGRAPHY(Point, 4326) NOT NULL,
    route_offset_m   DOUBLE PRECISION NOT NULL,
    accuracy_p50_m   REAL,
    fix_count        INTEGER NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, event_index)
);
CREATE INDEX IF NOT EXISTS walk_motion_event_location_gix
    ON walk_motion_event USING GIST (location);
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
