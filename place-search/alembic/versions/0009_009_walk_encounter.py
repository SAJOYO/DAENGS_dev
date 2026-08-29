"""009_walk_encounter.sql

원본 `migrations/009_walk_encounter.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "009_walk_encounter.sql"

SQL = """
-- 동선 주변 시설 관측 — 기하값까지만. 판정("지나쳤다/봤다")은 소비자(app/features/scene)의 일.
-- 밴드 3개를 전부 저장하는 이유: 원좌표는 finish 에서 지워지므로, 판정 반지름을
-- 실측 후 정하려면 후보 반지름들의 답이 미리 계산돼 있어야 한다 (재계산 불가 보상).
-- 폐업·인허가 상태는 필터가 아니라 데이터다(place_active) — 관측층은 큐레이션하지 않는다.
CREATE TABLE IF NOT EXISTS walk_encounter (
    session_id       TEXT NOT NULL REFERENCES walk_session (id) ON DELETE CASCADE,
    event_index      INTEGER NOT NULL,
    facility_source  TEXT NOT NULL,
    facility_ref     TEXT NOT NULL,            -- 안정 키 (source, source_ref). facility.id 아님
    kind             TEXT NOT NULL,
    lat              DOUBLE PRECISION NOT NULL,  -- 시설 대표점 (공개 장소)
    lng              DOUBLE PRECISION NOT NULL,
    place_active     BOOLEAN,                  -- 의료 오버레이 상태. 비의료·미링크는 NULL
    as_of            DATE,
    min_lateral_m    DOUBLE PRECISION NOT NULL,
    offset_m         DOUBLE PRECISION NOT NULL,
    dwell_s_10m      INTEGER NOT NULL,
    dwell_s_30m      INTEGER NOT NULL,
    dwell_s_50m      INTEGER NOT NULL,
    pass_count       INTEGER NOT NULL,
    stop_overlap_10m BOOLEAN NOT NULL,
    stop_overlap_30m BOOLEAN NOT NULL,
    stop_overlap_50m BOOLEAN NOT NULL,
    stop_s_10m       INTEGER NOT NULL DEFAULT 0,
    accuracy_p50_m   REAL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, event_index)
);
CREATE INDEX IF NOT EXISTS walk_encounter_facility_idx
    ON walk_encounter (facility_source, facility_ref);
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
