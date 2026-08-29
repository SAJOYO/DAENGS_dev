"""아이콘 그룹 매핑 — 지도 마커의 어휘 계약."""

import pytest

from app.geo.icons import _GROUPS, IconGroup, icon_group
from app.geo.schemas import PlaceOut
from app.place.facility_resolver import MEDICAL


def _place(kind: str) -> PlaceOut:
    return PlaceOut(
        id=1, kind=kind, name="x", lat=37.5, lng=127.0, distance_m=10,
        address=None, phone=None, is_night=False, is_24h=False,
        open_now=None, hours_today=None,
    )


@pytest.mark.parametrize("kind", MEDICAL)
def test_medical_kinds_share_one_group(kind):
    """병원과 약국은 같은 마커다 — 지도에서 의료를 한 눈에 잡는 게 먼저다."""
    assert icon_group(kind) == "medical"


def test_unknown_kind_is_etc_not_dropped():
    """원천이 늘면 모르는 kind 가 온다. 못 알아본 시설도 지도에는 있어야 한다."""
    assert icon_group("space_elevator") == "etc"


def test_split_supply_kinds_keep_the_existing_marker_contract():
    """분류는 갈라도 Android 마커 어휘는 이번 PR에서 늘리지 않는다."""
    assert icon_group("pet_shop") == "supply"
    assert icon_group("shopping") == "supply"
    assert icon_group("goods") == "etc"


def test_every_mapped_group_is_declared():
    """매핑 값이 IconGroup 리터럴 밖으로 새면 앱이 모르는 문자열을 받는다."""
    declared = set(IconGroup.__args__)
    assert set(_GROUPS.values()) <= declared
    assert "etc" in declared


def test_place_out_serializes_icon_group():
    """계산 필드라 응답에 실려야 한다 — 앱은 kind 가 아니라 이 값을 본다."""
    assert _place("pharmacy").model_dump()["icon_group"] == "medical"


def test_icon_group_cannot_disagree_with_kind():
    """입력으로 못 받는다: 두 값이 어긋난 응답 자체가 만들어지지 않아야 한다."""
    forced = PlaceOut(
        id=1, kind="hospital", name="x", lat=37.5, lng=127.0, distance_m=10,
        address=None, phone=None, is_night=False, is_24h=False,
        open_now=None, hours_today=None, icon_group="outdoor",
    )
    assert forced.icon_group == "medical"
