"""모호한 goods를 원천 카테고리 사실에 따라 둘로 분리한다.

KCISA의 `반려동물용품`과 KTO의 contenttypeid=38(쇼핑)은 서로 다른 시설이다. 과거에는
둘 다 `goods`로 적재되어 검색 후보군과 지도 탭에서 섞였다. facility.source는 적재 원천을
보존하므로 기존 행도 추측 없이 나눌 수 있다.

마지막 CHECK는 새 적재 코드나 수동 SQL이 모호한 `goods`를 다시 만들지 못하게 한다. 알려지지
않은 source의 goods 행이 있다면 제약 생성이 실패한다. 그 경우 임의로 둘 중 하나에 넣지 말고
원천 카테고리를 확인해 먼저 분류해야 한다.

분리 전에 만들어진 cross-source 링크는 `goods ↔ goods`였지만, 분리 뒤에는
`shopping ↔ pet_shop`이 된다. scalar kind만 제공하는 legacy 소비자가 한쪽을 숨기지 않도록
서로 다른 kind 링크도 함께 지운다. 링크는 원천에서 재계산하는 파생 데이터라 downgrade에서
복원하지 않는다.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_KIND = "goods"
PET_SHOP_SOURCE = "kcisa"
SHOPPING_SOURCE = "kto"
CONSTRAINT = "facility_kind_not_legacy_goods"


def upgrade() -> None:
    op.execute(
        f"UPDATE facility SET kind = 'pet_shop' "
        f"WHERE source = '{PET_SHOP_SOURCE}' AND kind = '{LEGACY_KIND}'"
    )
    op.execute(
        f"UPDATE facility SET kind = 'shopping' "
        f"WHERE source = '{SHOPPING_SOURCE}' AND kind = '{LEGACY_KIND}'"
    )
    op.execute("""
        DELETE FROM facility_link AS link
        USING facility AS winner, facility AS hidden
        WHERE link.source = 'facility'
          AND winner.id = link.facility_id
          AND hidden.id::text = link.source_ref
          AND winner.kind IS DISTINCT FROM hidden.kind
    """)
    op.create_check_constraint(CONSTRAINT, "facility", "kind <> 'goods'")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "facility", type_="check")
    op.execute(
        f"UPDATE facility SET kind = '{LEGACY_KIND}' "
        f"WHERE source = '{PET_SHOP_SOURCE}' AND kind = 'pet_shop'"
    )
    op.execute(
        f"UPDATE facility SET kind = '{LEGACY_KIND}' "
        f"WHERE source = '{SHOPPING_SOURCE}' AND kind = 'shopping'"
    )
