"""005_facility_link_multi_source.sql

원본 `migrations/005_facility_link_multi_source.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "005_facility_link_multi_source.sql"

SQL = """
-- 기반층 다원천화. facility는 원천별로 통째 교체하고(source 단위),
-- 링크는 특정 테이블 FK가 아니라 (source, source_ref) 쌍으로 어떤 원천이든 받는다.
ALTER TABLE facility ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'kcisa';
ALTER TABLE facility ADD COLUMN IF NOT EXISTS raw    JSONB;
CREATE INDEX IF NOT EXISTS facility_source_idx ON facility (source);

CREATE TABLE IF NOT EXISTS facility_link (
    facility_id BIGINT NOT NULL REFERENCES facility (id) ON DELETE CASCADE,
    source      TEXT NOT NULL,     -- 'mois:place'(의료 인허가) | 'facility'(원천 간 동일 시설) | 이후 원천
    source_ref  TEXT NOT NULL,     -- place.id / facility.id / 원천 고유키 — 문자열로 통일
    method      TEXT NOT NULL,
    distance_m  REAL,
    confidence  REAL,
    matched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (facility_id, source, source_ref)
);
CREATE INDEX IF NOT EXISTS facility_link_ref_idx ON facility_link (source, source_ref);

-- 기존 전용 링크 이전 후 폐기
INSERT INTO facility_link (facility_id, source, source_ref, method, distance_m, matched_at)
SELECT facility_id, 'mois:place', place_id::text, method, distance_m, matched_at
FROM facility_place_link
ON CONFLICT DO NOTHING;
DROP TABLE IF EXISTS facility_place_link;
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
