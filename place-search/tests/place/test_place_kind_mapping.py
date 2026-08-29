"""원천 카테고리에서 canonical kind로 가는 사실 매핑."""

from app.place.source_catalog import KCISA_KINDS, KTO_KINDS


def test_pet_supplies_and_general_shopping_are_distinct_kinds():
    assert KCISA_KINDS["반려동물용품"] == "pet_shop"
    assert KTO_KINDS["38"] == "shopping"


def test_ambiguous_goods_kind_is_no_longer_produced():
    assert "goods" not in KCISA_KINDS.values()
    assert "goods" not in KTO_KINDS.values()
