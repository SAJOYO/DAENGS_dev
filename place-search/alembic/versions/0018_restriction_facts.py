"""동반 제약을 자유 문장에서 파생한 술어로 저장한다.

`facility.pet->>'restrictions'` 는 23,914행에 있지만 읽는 코드가 없다. 결정 #70 의
`restriction_map` 등급이 그 문장을 술어로 옮기고, 이 리비전이 결과를 담을 자리를 만든다.

**두 축을 나눠 저장한다.**

    restriction_state   원천이 무엇을 말했나  unknown | none_confirmed | restricted
    parse_state         우리가 그것을 읽었나  mapped | partial | raw_only

한 컬럼으로 합치면 구분이 사라지는데, 그 둘은 사용자가 할 행동이 다르다.
KTO 9,692행은 원문 자체가 없어 `unknown` 이고 (전화로 확인해야 한다),
`조각공원은 동반 가능` 은 제한이 있는 것은 알지만 우리가 못 읽은 `restricted/raw_only` 다
(원문을 보여주면 된다). "모름" 과 "알지만 못 읽음" 은 다른 신뢰 상태다.

`predicates` 는 JSONB 배열이다. 별도 테이블을 만들지 않는 이유는 `pet_axes` 와 같다 —
행에 종속된 파생값이고 독립적으로 조회되지 않는다.

**컬럼은 값을 채우지 않는다.** 파생은 `python -m app.ingest restrictions` 가 하며,
이 리비전은 자리만 만든다. 마이그레이션 안에서 파생하면 규칙이 바뀔 때마다 리비전을
하나씩 더 쌓아야 하고, `semantics_version` 이 그 재파생을 관리하는 방식과 어긋난다.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESTRICTION_STATES = ("unknown", "none_confirmed", "restricted")
PARSE_STATES = ("mapped", "partial", "raw_only")


def upgrade() -> None:
    op.add_column(
        "facility",
        sa.Column("restriction_state", sa.Text(), nullable=True),
    )
    op.add_column(
        "facility",
        sa.Column("restriction_parse_state", sa.Text(), nullable=True),
    )
    op.add_column(
        "facility",
        sa.Column("restriction_predicates", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "facility",
        sa.Column("restriction_semantics_version", sa.Text(), nullable=True),
    )
    # 알려진 값만 들어간다. 오타가 조용히 저장되면 소비자가 그 행을 영영 못 읽는다.
    op.create_check_constraint(
        "facility_restriction_state_known",
        "facility",
        "restriction_state IS NULL OR restriction_state IN "
        f"({', '.join(repr(state) for state in RESTRICTION_STATES)})",
    )
    op.create_check_constraint(
        "facility_restriction_parse_state_known",
        "facility",
        "restriction_parse_state IS NULL OR restriction_parse_state IN "
        f"({', '.join(repr(state) for state in PARSE_STATES)})",
    )
    # `parse_state` 가 비는 경우는 **정확히 둘**이다: 아직 파생 안 됨(state IS NULL), 또는
    # 원문 자체가 없음(state='unknown' — 파싱할 대상이 없다). 그 밖에는 반드시 값이 있다.
    # 두 축을 "함께 채워지거나 함께 빈다" 로 묶으면 `unknown` 행이 표현 불가능해진다 —
    # KTO 9,692행이 그 상태다.
    op.create_check_constraint(
        "facility_restriction_parse_state_presence",
        "facility",
        "(restriction_parse_state IS NULL) = "
        "(restriction_state IS NULL OR restriction_state = 'unknown')",
    )
    # 버전 없는 파생값은 어느 규칙으로 만든 것인지 말하지 못한다 — 재분류 판단이 불가능해진다.
    op.create_check_constraint(
        "facility_restriction_version_present",
        "facility",
        "restriction_state IS NULL OR restriction_semantics_version IS NOT NULL",
    )
    # 소비자는 "아직 파생 안 된 행"을 찾는다. 부분 인덱스가 그 조회만 덮는다.
    op.create_index(
        "facility_restriction_pending_idx",
        "facility",
        ["id"],
        postgresql_where=sa.text("restriction_state IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("facility_restriction_pending_idx", table_name="facility")
    op.drop_constraint("facility_restriction_version_present", "facility", type_="check")
    op.drop_constraint("facility_restriction_parse_state_presence", "facility", type_="check")
    op.drop_constraint("facility_restriction_parse_state_known", "facility", type_="check")
    op.drop_constraint("facility_restriction_state_known", "facility", type_="check")
    op.drop_column("facility", "restriction_semantics_version")
    op.drop_column("facility", "restriction_predicates")
    op.drop_column("facility", "restriction_parse_state")
    op.drop_column("facility", "restriction_state")
