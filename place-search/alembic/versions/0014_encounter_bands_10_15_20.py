"""관측 밴드 10/30/50m → 10/15/20m

**왜 바꾸나**: 30m·50m 는 "그 앞을 지나갔다"의 후보 반지름으로 넓다. 50m 는 왕복 8차선을
건너 반대편 블록이어도 들어온다. 판정 반지름을 실측 후 고르려고 후보 3개를 저장하는 건
유지하되(009 주석), 후보 자체를 지나갈 만한 거리로 좁힌다.

**왜 단순 rename 이 아닌가**: 기존 행의 `dwell_s_30m` 에는 30m 원의 체류가 들어 있다.
컬럼만 `dwell_s_15m` 으로 바꾸면 그 값이 15m 원의 답인 척하게 된다 — 이 레포가 계속
없애온 조용한 거짓말이다. 원좌표는 finish 에서 이미 purge 됐으므로 재계산도 불가능하다.
그래서 `ENCOUNTER_OCCURRENCE_VERSION` 을 3 으로 올리고 기존 행은 2 에 남긴다.
`judge()` 가 현재 버전보다 낮은 행을 `unjudgeable` 로 돌리므로, 옛 값이 새 밴드의
답으로 읽히는 경로가 없다. v1 집계행을 다룰 때와 같은 방식이다 (010).

`dwell_s_10m` · `stop_overlap_10m` · `stop_s_10m` 은 반지름이 그대로라 의미가 안 바뀐다.
이름도 값도 건드리지 않는다.

**후보 버퍼도 같이 좁힌다**: `facility_candidates()` 의 `ST_DWithin` 이 50m 였다. 최대
밴드보다 넓게 뽑으면 어떤 밴드에도 못 들어오는 시설을 매번 끌어와 버린다. 그 값은
`app/features/walk/store.py` 에 있고 이 리비전과 짝이다.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMES = (
    ("dwell_s_30m", "dwell_s_15m"),
    ("dwell_s_50m", "dwell_s_20m"),
    ("stop_overlap_30m", "stop_overlap_15m"),
    ("stop_overlap_50m", "stop_overlap_20m"),
)


def upgrade() -> None:
    for old, new in RENAMES:
        op.alter_column("walk_encounter", old, new_column_name=new)


def downgrade() -> None:
    for old, new in RENAMES:
        op.alter_column("walk_encounter", new, new_column_name=old)
