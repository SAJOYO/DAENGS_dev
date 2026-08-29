"""011_walk_fix_chain.sql

원본 `migrations/011_walk_fix_chain.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "011_walk_fix_chain.sql"

SQL = """
-- pause/resume 경계를 원좌표와 함께 보존한다. 같은 세션의 두 chain 사이에는
-- 시간·거리가 가까워도 segment가 없다. 기존 클라이언트는 단일 chain(0)으로 읽는다.
ALTER TABLE walk_fix
    ADD COLUMN IF NOT EXISTS chain_index INTEGER NOT NULL DEFAULT 0;

DO $$ BEGIN
    ALTER TABLE walk_fix
        ADD CONSTRAINT walk_fix_chain_index_nonnegative CHECK (chain_index >= 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
