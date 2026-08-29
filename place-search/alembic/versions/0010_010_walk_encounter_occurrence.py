"""010_walk_encounter_occurrence.sql

원본 `migrations/010_walk_encounter_occurrence.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "010_walk_encounter_occurrence.sql"

SQL = """
-- 시설별 세션 합계(v1)를 연속 진입 occurrence(v2)로 바꾼다.
-- 기존 v1 행은 원좌표가 이미 삭제돼 분할할 수 없으므로 occurrence_version=1로 남긴다.
ALTER TABLE walk_encounter
    ADD COLUMN IF NOT EXISTS occurrence_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS occurrence_index INTEGER,
    ADD COLUMN IF NOT EXISTS entered_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS exited_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS entry_observed BOOLEAN,
    ADD COLUMN IF NOT EXISTS exit_observed BOOLEAN,
    ADD COLUMN IF NOT EXISTS entered_offset_m DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS exited_offset_m DOUBLE PRECISION;

DO $$ BEGIN
    ALTER TABLE walk_encounter ADD CONSTRAINT walk_encounter_occurrence_version_check
        CHECK (occurrence_version >= 1);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE walk_encounter ADD CONSTRAINT walk_encounter_occurrence_v2_check
        CHECK (
            occurrence_version < 2 OR (
                occurrence_index IS NOT NULL AND occurrence_index >= 0
                AND entered_at IS NOT NULL AND exited_at IS NOT NULL
                AND entered_at <= exited_at
                AND entry_observed IS NOT NULL AND exit_observed IS NOT NULL
                AND entered_offset_m IS NOT NULL AND entered_offset_m >= 0
                AND exited_offset_m IS NOT NULL AND exited_offset_m >= entered_offset_m
                AND pass_count = 1
            )
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS walk_encounter_occurrence_uidx
    ON walk_encounter (session_id, facility_source, facility_ref, occurrence_index)
    WHERE occurrence_version >= 2;
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
