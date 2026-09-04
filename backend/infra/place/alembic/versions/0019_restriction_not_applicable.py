"""`해당없음`을 확인된 제한 없음과 분리한다.

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CONSTRAINT = "facility_restriction_state_known"
_CONSTRAINT = "facility_restriction_state_v2_known"


def upgrade() -> None:
    op.drop_constraint(_OLD_CONSTRAINT, "facility", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "facility",
        "restriction_state IS NULL OR restriction_state IN "
        "('unknown', 'none_confirmed', 'not_applicable', 'restricted')",
    )


def downgrade() -> None:
    # 옛 계약에는 이 상태를 담을 칸이 없다. 기존 동작으로 접은 뒤 제약을 되돌린다.
    op.execute("""
        UPDATE facility SET restriction_state = 'none_confirmed'
        WHERE restriction_state = 'not_applicable'
    """)
    op.drop_constraint(_CONSTRAINT, "facility", type_="check")
    op.create_check_constraint(
        _OLD_CONSTRAINT,
        "facility",
        "restriction_state IS NULL OR restriction_state IN "
        "('unknown', 'none_confirmed', 'restricted')",
    )
