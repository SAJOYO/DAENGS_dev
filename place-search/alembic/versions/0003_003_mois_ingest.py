"""003_mois_ingest.sql

원본 `migrations/003_mois_ingest.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "003_mois_ingest.sql"

SQL = """
-- 행정안전부 동물병원/동물약국 OpenAPI 동기화 메타데이터.
ALTER TABLE place ADD COLUMN IF NOT EXISTS source_updated_at   TIMESTAMPTZ;
ALTER TABLE place ADD COLUMN IF NOT EXISTS license_status_code TEXT;
ALTER TABLE place ADD COLUMN IF NOT EXISTS license_status_name TEXT;
ALTER TABLE place ADD COLUMN IF NOT EXISTS coordinate_source   TEXT;
ALTER TABLE place ADD COLUMN IF NOT EXISTS raw_data            JSONB;

CREATE INDEX IF NOT EXISTS place_source_updated_idx
    ON place (source, source_updated_at);

CREATE TABLE IF NOT EXISTS ingest_state (
    source      TEXT PRIMARY KEY,
    watermark   TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
