"""미시 관측 층 — 판정 이전의 후보 구간과 이동 속도 분포

설계·근거 `app/features/walk/observation.py`.

**왜 필요한가**: `finalize` 는 파생 사실을 쓴 뒤 원좌표를 지운다(결정 #57). 지금 미시 쪽에
영구히 남는 것은 `walk_motion_event` 뿐인데 그건 `0.5 m/s · 10 초` 로 **이미 판정된 정지**다.
그 문턱이 옳은지가 아직 안 정해졌으므로(M2 부정 결과,
`docs/research/2026-08-27-latent-dwell-synthesis.md`), 오늘 수집하는 산책은 **문턱을 다시
고를 방법이 없는 과거**가 된다. 이 리비전이 그 앞의 층을 만든다.

**왜 `walk_motion_event` 를 넓히지 않는가**: 그 테이블은 "정지" 라는 **판정 결과**이고 바깥
계약(`MotionEventOccurrence`)으로 나간다 — 문턱을 바꾸면 `stop_count` 의 뜻이 조용히 달라진다.
후보 구간은 판정이 아니라 그 앞의 재료라 뜻이 다르고, 둘을 한 테이블에 담으면 "정지가
무엇인가" 가 흐려진다. 나중에 지표 승자가 정해지면 `walk_motion_event` 를 이 층의 투영으로
바꾸는 선택지가 남아 있고, 그때 계약을 깨는 것이 **보이는 결정**이 된다.

**왜 좌표를 남기나**: 미시 사건은 점이라 원좌표를 그대로 들 수 있다 — 궤적이 아니다.
`walk_motion_event` 가 이미 같은 이유로 좌표를 남긴다. 후보 구간은 산책 전체가 아니라
느렸던 자리만이므로 이 층으로 동선이 복원되지 않는다.

**왜 `walk_facts` 에 속도 분위수를 더하나**: 초과시간 지표는 기준 속도 `v_ref` 가 필요한데
추정 방식이 아직 안 정해졌다. `avg_speed_mps` 는 평균 하나뿐이라 만성 저속이 섞이면 같이
낮아진다. 분위수를 남겨야 나중에 자료로 고를 수 있다 — 곡선(`0015`)과 같은 자리, 같은 이유.

**왜 NULL 을 허용하나**: 이 리비전 이전에 확정된 세션은 원좌표가 이미 없어 만들 수 없다.
채울 수 없는 과거를 0 으로 채우면 "느린 구간이 없었다" 는 거짓이 된다.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "walk_micro_observation",
        sa.Column("session_id", sa.Text(), sa.ForeignKey("walk_session.id",
                                                         ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("observation_index", sa.Integer(), primary_key=True),
        # 버전은 행마다 남는다 — 문턱이 바뀌면 세대가 다른 관측을 한 분포에 섞으면 안 된다
        sa.Column("observation_version", sa.Integer(), nullable=False),
        # slow = 관측 중 느렸다 / gap = 관측이 없었다. 이 둘을 섞으면 신호 음영이
        # 최고의 가짜 체류가 된다
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_s", sa.Numeric(10, 2), nullable=False),
        sa.Column("location", Geography("POINT", srid=4326), nullable=False),
        sa.Column("path_m", sa.Numeric(12, 3), nullable=False),
        sa.Column("net_m", sa.Numeric(12, 3), nullable=False),
        sa.Column("span_m", sa.Numeric(12, 3), nullable=False),
        sa.Column("fix_count", sa.Integer(), nullable=False),
        sa.Column("accuracy_p50_m", sa.Numeric(8, 2)),
        sa.Column("route_offset_m", sa.Numeric(12, 3), nullable=False),
        sa.Column("chain_index", sa.Integer(), nullable=False),
        sa.Column("abuts_break", sa.Boolean(), nullable=False),
        sa.CheckConstraint("kind IN ('slow', 'gap')", name="walk_micro_observation_kind"),
        sa.CheckConstraint("ended_at >= started_at",
                           name="walk_micro_observation_time_order"),
        # gap 은 관측이 없다 — 창 안 거리가 있을 수 없다. 섞이면 층의 뜻이 무너진다
        sa.CheckConstraint("kind <> 'gap' OR (path_m = 0 AND span_m = 0)",
                           name="walk_micro_observation_gap_has_no_path"),
    )
    op.create_index("walk_micro_observation_location_idx", "walk_micro_observation",
                    ["location"], postgresql_using="gist")

    op.add_column("walk_facts", sa.Column("speed_profile", postgresql.JSONB()))
    op.add_column("walk_facts", sa.Column("speed_profile_version", sa.Integer()))
    op.create_check_constraint(
        "walk_facts_speed_profile_paired",
        "walk_facts",
        "(speed_profile IS NULL) = (speed_profile_version IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("walk_facts_speed_profile_paired", "walk_facts", type_="check")
    op.drop_column("walk_facts", "speed_profile_version")
    op.drop_column("walk_facts", "speed_profile")
    op.drop_index("walk_micro_observation_location_idx",
                  table_name="walk_micro_observation")
    op.drop_table("walk_micro_observation")
