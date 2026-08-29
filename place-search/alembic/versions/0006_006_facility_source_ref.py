"""006_facility_source_ref.sql

원본 `migrations/006_facility_source_ref.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "006_facility_source_ref.sql"

SQL = """
-- 안정 식별자. facility.id(BIGSERIAL)는 스냅샷 교체마다 바뀌므로 외부가 잡으면 안 된다.
-- 원천 고유키를 1급으로 올리고, 적재는 (source, source_ref) UPSERT로 바꾼다 →
-- id가 교체 사이에 유지되고, 즐겨찾기·추천 이력이 나중에 붙어도 깨지지 않는다.
ALTER TABLE facility ADD COLUMN IF NOT EXISTS source_ref TEXT;
ALTER TABLE facility ADD COLUMN IF NOT EXISTS synced_at  TIMESTAMPTZ;

-- 이번 마이그레이션 시점의 기존 행은 ref가 없다. 적재를 다시 돌리면 UPSERT가 ref를 채우고,
-- 못 채운 행(원천에서 사라진 것)은 prune이 지운다. 그때까지만 NULL을 허용한다.
CREATE UNIQUE INDEX IF NOT EXISTS facility_source_ref_uidx
    ON facility (source, source_ref) WHERE source_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS facility_synced_idx ON facility (source, synced_at);
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
