"""001_init.sql

원본 `migrations/001_init.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0001
Revises: (없음 — 최초)
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "001_init.sql"

SQL = """
-- DAENGS_geo 초기 스키마. docker-compose 첫 기동 시 자동 실행.
CREATE EXTENSION IF NOT EXISTS postgis;

-- 병원/약국 POI. 원천은 공공데이터(지방행정 인허가). 제공사 로컬검색 결과 저장 금지.
CREATE TABLE IF NOT EXISTS place (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('hospital', 'pharmacy')),
    name          TEXT NOT NULL,
    address       TEXT,
    phone         TEXT,
    location      geography(Point, 4326) NOT NULL,
    is_night      BOOLEAN NOT NULL DEFAULT FALSE,   -- 야간 진료 표방 (영업시간과 별개 플래그)
    is_24h        BOOLEAN NOT NULL DEFAULT FALSE,
    hours         JSONB,                             -- app/geo/hours.py 형식. NULL = 미상
    source        TEXT NOT NULL,                     -- 'public:localdata' 등
    source_id     TEXT,                              -- 원천 식별자 (중복 적재 방지)
    active        BOOLEAN NOT NULL DEFAULT TRUE,     -- 폐업 = FALSE
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS place_location_gix ON place USING GIST (location);
CREATE INDEX IF NOT EXISTS place_kind_idx     ON place (kind) WHERE active;
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
