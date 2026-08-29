"""place.tags 에서 과목 어휘를 지운다 (결정 #64)

한국 수의 진료에는 과목(전문의) 제도가 없다. 이 태그들은 자격이 아니라 **간판 문구**를
정규식으로 주운 것이었고, 실측에서도 활성 여부와 무관하게 28,284곳 중 109곳(0.39%)에만
붙어 있었다 — ortho 4 · dental 20 · eye 19 · cardio 14 · derma 11 · rehab 11 · surgery 30.

`app/geo/tagging.py` 에서 규칙을 지웠으므로 앞으로 적재되는 행에는 안 붙는다. 이 리비전은
**이미 붙어 있는 것**을 지운다. 안 지우면 `require` 로 그 태그를 명시한 요청이 계속 먹히고,
어휘에서 사라진 값이 데이터에는 살아 있는 상태가 된다.

**왜 되돌릴 수 없나**: 태그는 이름에서 파생한 값이라 원본(사업장명)이 그대로 남아 있다.
downgrade 는 규칙을 되살려 재적재하는 일이지 이 리비전이 할 일이 아니다.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SPECIALTY_TAGS = ("ortho", "eye", "dental", "derma", "cardio", "rehab", "surgery")


def upgrade() -> None:
    for tag in SPECIALTY_TAGS:
        op.execute(f"UPDATE place SET tags = array_remove(tags, '{tag}') WHERE tags @> ARRAY['{tag}']")


def downgrade() -> None:
    """복원하지 않는다 — 사업장명이 원본이므로 재적재가 정답이다."""
