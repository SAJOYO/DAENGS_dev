"""점령 게임의 중립 지점을 Territory Site 어휘로 승격한다.

과거 0012 리비전은 그대로 두고 현재 테이블·식별자만 전진시킨다. 기존 행의 격자 반경은
바꾸지 않는다. 115u 행을 140u라고 이름만 바꾸면 같은 ID가 다른 공간을 뜻하기 때문이다.
140u 게임판은 ``daengs_place.ingest.territory_sites``로 원본에서 다시 선별한다.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("anchor", "territory_site")
    op.alter_column("territory_site", "cell", new_column_name="site_id")
    op.execute(
        "UPDATE territory_site "
        "SET site_id = regexp_replace(site_id, '^anchor-hex:', 'territory-site:hex-v1:') "
        "WHERE site_id LIKE 'anchor-hex:%'"
    )

    op.drop_constraint("anchor_source_cell_key", "territory_site", type_="unique")
    op.create_unique_constraint(
        "territory_site_site_id_key",
        "territory_site",
        ["site_id"],
    )
    op.execute("ALTER TABLE territory_site RENAME CONSTRAINT anchor_pkey TO territory_site_pkey")
    op.execute("ALTER INDEX anchor_gix RENAME TO territory_site_gix")
    op.execute("ALTER INDEX anchor_kind_idx RENAME TO territory_site_kind_idx")
    op.execute("ALTER SEQUENCE anchor_id_seq RENAME TO territory_site_id_seq")


def downgrade() -> None:
    op.execute("ALTER SEQUENCE territory_site_id_seq RENAME TO anchor_id_seq")
    op.execute("ALTER INDEX territory_site_kind_idx RENAME TO anchor_kind_idx")
    op.execute("ALTER INDEX territory_site_gix RENAME TO anchor_gix")
    op.execute("ALTER TABLE territory_site RENAME CONSTRAINT territory_site_pkey TO anchor_pkey")
    op.drop_constraint("territory_site_site_id_key", "territory_site", type_="unique")
    op.create_unique_constraint(
        "anchor_source_cell_key",
        "territory_site",
        ["source", "site_id"],
    )
    op.execute(
        "UPDATE territory_site "
        "SET site_id = regexp_replace(site_id, '^territory-site:hex-v1:', 'anchor-hex:') "
        "WHERE site_id LIKE 'territory-site:hex-v1:%'"
    )
    op.alter_column("territory_site", "site_id", new_column_name="cell")
    op.rename_table("territory_site", "anchor")
