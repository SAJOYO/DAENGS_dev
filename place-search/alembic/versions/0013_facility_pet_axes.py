"""facility.pet 봉투 → 필터 가능한 축

설계 docs/explorations/facility/pet-axes.md,
근거 측정 docs/research/2026-08-24-facility-pet-coverage.md (2026-08-24).

**앞 리비전들과 다르다.** 0001~0012 는 `migrations/*.sql` 을 그대로 옮긴 역사라 되돌리지
않지만, 이건 alembic 도입 뒤 처음 만드는 변경이라 `downgrade()` 가 실제로 동작한다.

**왜 원문을 남기는가**: 파싱이 틀렸을 때 되돌릴 근거가 있어야 하고, `restrictions` 같은
자유 서술은 어차피 표시용으로 계속 쓴다. 축은 `pet` 을 대체하지 않고 옆에 선다.

**왜 여기서 값을 안 채우는가**: 값이 `5kg 미만 소형` · `소형/중형` · `모두 가능, 고양이`
처럼 섞여 있어 SQL 정규식 한 줄로 안 떨어진다. 파싱은 `app/geo/pet.py` 한 곳이고 이 리비전은
자리만 만든다. 채우기는 `python -m app.ingest pet-axes` — 원천을 다시 받지 않는다.

**왜 인덱스를 안 만드는가**: 이 컬럼들은 항상 `ST_DWithin` 뒤에 붙는 후처리 조건이다.
후보를 좁히는 건 GIST 지리 인덱스이고, 그 뒤 수십~수백 행 필터는 인덱스가 필요 없다.
측정 없이 인덱스를 늘리면 적재 UPSERT 만 느려진다 — 필요해지면 그때 재고 만든다.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIZE_CLASSES = ("small", "medium", "large", "any")

COLUMNS = (
    # NULL = 미상. 어느 컬럼도 '모름'을 '아님'으로 만들지 않는다.
    sa.Column("pet_allowed", sa.Boolean()),
    sa.Column("pet_exclusive", sa.Boolean()),
    # NULL = 종 표기 없음(개 전제), FALSE = 종을 열거하면서 개를 뺀 곳
    sa.Column("pet_dog_ok", sa.Boolean()),
    sa.Column("pet_size_class", sa.Text()),      # small < medium < large < any
    sa.Column("pet_max_kg", sa.Numeric()),       # kg 표기가 있을 때만
)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("facility", column)
    allowed = ", ".join(f"'{value}'" for value in SIZE_CLASSES)
    op.create_check_constraint(
        "facility_pet_size_class_known",
        "facility",
        f"pet_size_class IS NULL OR pet_size_class IN ({allowed})",
    )
    op.create_check_constraint(
        "facility_pet_max_kg_positive",
        "facility",
        "pet_max_kg IS NULL OR pet_max_kg > 0",
    )


def downgrade() -> None:
    op.drop_constraint("facility_pet_max_kg_positive", "facility", type_="check")
    op.drop_constraint("facility_pet_size_class_known", "facility", type_="check")
    for column in reversed(COLUMNS):
        op.drop_column("facility", column.name)
