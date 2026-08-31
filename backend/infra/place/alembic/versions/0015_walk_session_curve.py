"""walk_facts 에 무좌표 진행 곡선

설계·근거 `app/features/walk/curve.py`.

**왜 새 테이블이 아닌가**: 세션당 한 행이고 수명이 `walk_facts` 와 같다. 같은 행에 이미
`quality` JSONB 가 "계약 밖 운영 데이터" 로 앉아 있으므로 자리도 같다. 테이블을 늘리면
cascade 와 조회가 하나씩 더 늘 뿐 얻는 게 없다.

**왜 `WalkFacts` 모델에 안 넣는가**: 그건 바깥에 주는 계약이고 `test_walk_contract.py` 가
필드 집합을 고정한다. 곡선은 아직 내부 파생이라 계약에 올릴 근거가 없다 — 올릴 때가 되면
그때 계약을 깨는 것이 보이는 결정이 된다.

**왜 NULL 을 허용하나**: 이 리비전 이전에 확정된 세션은 원좌표가 이미 지워져 곡선을 만들 수
없다. 채울 수 없는 과거를 0 으로 채우면 "평탄하게 걸었다" 는 거짓이 된다.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("walk_facts", sa.Column("curve", postgresql.JSONB()))
    op.add_column("walk_facts", sa.Column("curve_version", sa.Integer()))
    op.create_check_constraint(
        "walk_facts_curve_paired",
        "walk_facts",
        "(curve IS NULL) = (curve_version IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("walk_facts_curve_paired", "walk_facts", type_="check")
    op.drop_column("walk_facts", "curve_version")
    op.drop_column("walk_facts", "curve")
