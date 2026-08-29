"""002_tags_scale.sql

원본 `migrations/002_tags_scale.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "002_tags_scale.sql"

SQL = """
-- name-tagging + 인허가 규모 지표
ALTER TABLE place ADD COLUMN IF NOT EXISTS tags        TEXT[]  NOT NULL DEFAULT '{}';
ALTER TABLE place ADD COLUMN IF NOT EXISTS area_m2     NUMERIC;      -- 인허가 면적 (규모 표시용)
ALTER TABLE place ADD COLUMN IF NOT EXISTS staff_count INTEGER;      -- 인허가 종사자수
CREATE INDEX IF NOT EXISTS place_tags_gin ON place USING GIN (tags);
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
