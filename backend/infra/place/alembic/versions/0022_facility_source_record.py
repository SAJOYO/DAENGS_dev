"""원천 snapshot과 detail 획득 상태를 제품 facility와 분리해 보존한다.

`facility`는 검색 대상이라 KCISA의 확정 불허 행을 제외하고, 여러 원천 값을 빌려서 보여준다.
그 테이블을 원천 사실의 권위로 쓰면 제외된 행과 KTO 상세 실패 원인이 사라진다. 이 shadow
테이블은 `(source, record_ref)` 원천 행 자체를 보존한다. `source_ref`는 제품 행과 연결하는
별도 키다. projector 결과는 저장하지 않는다.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DETAIL_STATES = (
    "not_applicable",
    "not_fetched",
    "fetched",
    "no_data",
    "fetch_failed",
    "unknown",
)


def upgrade() -> None:
    op.create_table(
        "facility_source_record",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("record_ref", sa.Text(), primary_key=True),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("listing_raw", postgresql.JSONB(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("detail_raw", postgresql.JSONB()),
        sa.Column("detail_state", sa.Text(), nullable=False),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail_attempted_at", sa.DateTime(timezone=True)),
        sa.Column("detail_fetched_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "occurrence_count > 0",
            name="facility_source_record_occurrence_positive",
        ),
        sa.CheckConstraint(
            f"detail_state IN ({', '.join(repr(state) for state in DETAIL_STATES)})",
            name="facility_source_record_detail_state_known",
        ),
        # fetched만 payload를 가진다. 실패/no-data를 빈 객체로 위장하지 않는다.
        sa.CheckConstraint(
            "(detail_state = 'fetched') = (detail_raw IS NOT NULL)",
            name="facility_source_record_detail_payload_matches_state",
        ),
        sa.CheckConstraint(
            "(detail_state IN ('fetched', 'no_data', 'fetch_failed')) = "
            "(detail_attempted_at IS NOT NULL)",
            name="facility_source_record_attempt_time_matches_state",
        ),
        sa.CheckConstraint(
            "(detail_state = 'fetched') = (detail_fetched_at IS NOT NULL)",
            name="facility_source_record_fetched_time_matches_state",
        ),
    )
    op.create_index(
        "facility_source_record_source_ref_idx",
        "facility_source_record",
        ["source", "source_ref"],
    )
    op.create_index(
        "facility_source_record_observed_idx",
        "facility_source_record",
        ["source", "observed_at"],
    )
    op.create_index(
        "facility_source_record_detail_pending_idx",
        "facility_source_record",
        ["source", "record_ref"],
        postgresql_where=sa.text("detail_state IN ('not_fetched', 'fetch_failed', 'unknown')"),
    )

    # KTO 목록 원문은 이미 raw에 있다. 기존 pet={}의 원인은 복원할 수 없어 unknown이다.
    op.execute("""
        INSERT INTO facility_source_record (
            source, record_ref, source_ref, listing_raw, occurrence_count,
            detail_raw, detail_state,
            snapshot, observed_at, detail_attempted_at, detail_fetched_at
        )
        SELECT source, source_ref, source_ref, raw, 1,
               CASE WHEN pet <> '{}'::jsonb THEN pet END,
               CASE WHEN pet <> '{}'::jsonb THEN 'fetched' ELSE 'unknown' END,
               snapshot, COALESCE(synced_at, now()),
               CASE WHEN pet <> '{}'::jsonb THEN COALESCE(synced_at, now()) END,
               CASE WHEN pet <> '{}'::jsonb THEN COALESCE(synced_at, now()) END
        FROM facility
        WHERE source = 'kto' AND source_ref IS NOT NULL AND raw IS NOT NULL
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index(
        "facility_source_record_detail_pending_idx",
        table_name="facility_source_record",
    )
    op.drop_index(
        "facility_source_record_observed_idx",
        table_name="facility_source_record",
    )
    op.drop_index(
        "facility_source_record_source_ref_idx",
        table_name="facility_source_record",
    )
    op.drop_table("facility_source_record")
