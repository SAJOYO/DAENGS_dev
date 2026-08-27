"""검색 상태(`EditableState`)의 요청 계약 — 버전·경계·되돌림.

이 파일이 깨지는 이유는 하나다: **클라이언트가 왕복시키는 state 의 해석이 바뀌었다.**
HTTP 표면(입력 모델·상태코드)은 `tests/api/test_input_validation.py` 가 따로 지킨다.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.discovery.refine import tools
from app.discovery.refine.engine import refine
from app.discovery.refine.nl import ToolCall
from app.discovery.refine.tools import ToolInputError
from app.discovery.state import CURRENT_STATE_VERSION, EditableState
from app.features.hospital.api import Edit, HospitalSearchIn, hospital_search
from tests.conftest import TEST_ORIGIN, seeded_places


def test_legacy_state_is_migrated_without_silent_loss():
    """
    Contract: 옛 필드명(night/emergency/at)은 현재 필드로 옮겨지고, 값이 조용히 사라지지 않는다.
    Decision: #35
    """
    old = {
        "lat": 37.5,
        "lng": 127.0,
        "target": {
            "night": True,
            "emergency": True,
            "at": "2026-08-21T12:00:00Z",
        },
    }

    state = EditableState.model_validate(old)

    assert state.state_version == CURRENT_STATE_VERSION
    assert state.target.night_service is True
    assert state.target.emergency_service is True
    assert state.time_intent is not None
    assert state.time_intent.kind == "service_at"
    assert state.time_intent.at == datetime.fromisoformat("2026-08-21T12:00:00+00:00")


def test_removed_axes_are_dropped_instead_of_rejecting_an_old_client():
    """
    Contract: 없앤 축을 담은 옛 state 는 422 가 아니라 **그 필드만 버리고** 통과한다.

    Decision: #64, #66

    이걸 안 하면 축을 하나 없앨 때마다 화면을 켜 두고 있던 클라이언트가 전부 422 를 맞는다.
    `extra=forbid` 는 유지된다 — 아래 오타 검사가 그것을 본다.
    """
    old = {
        "state_version": 3,
        "lat": 37.5, "lng": 127.0,
        "target": {"specialty": ["ortho"]},                     # v2 축 (#64)
        "journey": {"walk": {"option": "no_stairs", "avoid": ["stairs"],
                             "max_walk_min": 10}},              # v3 축 (#66)
    }

    state = EditableState.model_validate(old)

    assert state.state_version == CURRENT_STATE_VERSION
    assert state.journey.walk.max_walk_min == 10, "같이 온 살아 있는 값까지 버렸다"
    with pytest.raises(ValidationError):
        EditableState.model_validate({"lat": 37.5, "lng": 127.0,
                                      "journey": {"walk": {"opshun": "x"}}})


def test_removed_axes_are_dropped_inside_history_too():
    """
    Contract: undo 스택의 옛 스냅샷도 같은 이행을 탄다.

    Decision: #66

    `validate_history_snapshots` 가 각 스냅샷을 같은 계약으로 재검증하므로 저절로 따라온다.
    저절로 따라오는 것과 따라온다고 **믿는 것**은 다르므로 여기서 고정한다.
    """
    state = EditableState.model_validate({
        "state_version": 3,
        "lat": 37.5, "lng": 127.0,
        "history": [{"state_version": 3, "lat": 37.5, "lng": 127.0,
                     "journey": {"walk": {"avoid": ["underpass"]}}}],
    })

    assert len(state.history) == 1
    assert "avoid" not in state.history[0]["journey"]["walk"]


def test_current_unversioned_state_is_stamped_and_unknown_fields_are_rejected():
    """
    Contract: 버전 없는 state 는 현재 버전으로 찍고, 모르는 필드·모르는 버전은 거부한다.
    Decision: #35
    """
    state = EditableState.model_validate({
        "lat": 37.5,
        "lng": 127.0,
        "target": {"night_service": True},
    })
    assert state.state_version == CURRENT_STATE_VERSION

    with pytest.raises(ValidationError):
        EditableState.model_validate({"lat": 37.5, "lng": 127.0, "typo": True})
    with pytest.raises(ValidationError):
        EditableState.model_validate({"state_version": 999, "lat": 37.5, "lng": 127.0})


@pytest.mark.parametrize("patch", [
    {"lat": 91, "lng": 127},
    {"lat": 37, "lng": 181},
    {"lat": 37, "lng": 127, "target": {"radius_m": 99_999_999}},
    {"lat": 37, "lng": 127, "target": {"limit": 999_999}},
    {"lat": 37, "lng": 127, "history": [{}] * 11},
])
def test_client_state_bounds_are_enforced(patch):
    """
    Contract: 전 필드에 상·하한이 있다. 무상태 서버라 state 는 클라이언트가 준 입력이다.
    Decision: #35
    """
    with pytest.raises(ValidationError):
        EditableState.model_validate(patch)


def test_history_snapshots_are_migrated_and_validated_too():
    """
    Contract: history 안의 스냅샷도 같은 마이그레이션·검증을 받는다. 재귀적으로 적용된다.
    Decision: #35
    """
    state = EditableState.model_validate({
        "lat": 37.5,
        "lng": 127.0,
        "history": [{"lat": 37.4, "lng": 127.1, "target": {"night": True}}],
    })
    assert state.history[0]["state_version"] == CURRENT_STATE_VERSION
    assert state.history[0]["target"]["night_service"] is True

    with pytest.raises(ValidationError):
        EditableState.model_validate({
            "lat": 37.5,
            "lng": 127.0,
            "history": [{"lat": 999, "lng": 127.1}],
        })


async def test_request_origin_overrides_round_tripped_state_without_history_entry():
    """
    Contract: 요청 origin 은 state 좌표를 덮되 되돌림 지점을 찍지 않는다 — GPS 갱신은
              사용자 편집이 아니다. 넘겨받은 state 객체 자체도 변형하지 않는다.
    Decision: #37, #46
    """
    old = EditableState(lat=37.0, lng=127.0)

    result = await refine(
        old, edits=[], utterance=None, shown_ids=[], profile=None,
        lat=35.0, lng=129.0, default_radius=2000,
    )

    assert (result.state.lat, result.state.lng) == (35.0, 129.0)
    assert result.state.history == []
    assert (old.lat, old.lng) == (37.0, 127.0)


async def test_omitting_origin_keeps_the_pinned_search_location():
    """지도를 끌어 그 지역을 검색한 뒤(pinned), 필터만 바꾸는 턴에서 좌표가 튀면 안 된다.

    **위치는 두 경로로 들어온다.** 앱이 이 둘을 섞으면 사용자가 팬해서 보던 지역이
    필터 하나 바꿀 때마다 내 위치로 되돌아간다.

        요청 origin 있음  "지금 내 위치" — GPS 갱신. state 좌표를 덮는다
        요청 origin 생략  "보던 데 그대로" — state 좌표를 유지한다 (pinned)

    앱은 pinned 상태에서 origin 을 **보내지 않아야** 한다. 습관적으로 최신 GPS 를 실으면
    위 첫 줄이 매 턴 발동한다.

    Contract: origin 생략은 "state 좌표 유지"다. 앱의 deviceLocation/searchOrigin 분리가
              서버에서 성립하는 지점.
    Decision: #46
    """
    async with seeded_places([]) as db:
        pinned = EditableState(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1])
        out = await hospital_search(
            HospitalSearchIn(
                state=pinned, transport="none",
                edits=[Edit(tool="set_radius", args={"m": 1500})],
            ),
            db,
        )

    assert (out.state.lat, out.state.lng) == TEST_ORIGIN
    assert out.state.target.radius_m == 1500


async def test_map_pan_is_undoable_but_a_gps_refresh_is_not():
    """같은 좌표 이동이라도 **누가 시켰나**에 따라 되돌림 대상이 갈린다.

    Contract: 지도 팬은 명시적 편집이라 되돌릴 수 있고, GPS 갱신은 기기 사실이라
              history 에 안 들어간다.
    Decision: #37, #46
    """
    at_home = EditableState(lat=37.0, lng=127.0)

    gps = await refine(at_home, edits=[], utterance=None, shown_ids=[], profile=None,
                       lat=35.0, lng=129.0, default_radius=2000)
    assert (gps.state.lat, gps.state.lng) == (35.0, 129.0)
    assert gps.state.history == [], "GPS 갱신은 사용자가 되돌릴 편집이 아니다"

    pan = await refine(at_home, edits=[ToolCall("set_origin", {"lat": 35.0, "lng": 129.0})],
                       utterance=None, shown_ids=[], profile=None,
                       lat=37.0, lng=127.0, default_radius=2000)
    assert (pan.state.lat, pan.state.lng) == (35.0, 129.0)
    back = tools.undo(pan.state)
    assert (back.lat, back.lng) == (37.0, 127.0), "지도 팬은 명시적 의도라 되돌릴 수 있어야 한다"


def test_legacy_set_time_tool_is_migrated_but_not_advertised():
    """
    Contract: 폐기된 툴 이름은 받아서 옮겨주되 현재 툴 목록에는 노출하지 않는다.
    Decision: #35
    """
    state = tools.apply(
        EditableState(lat=37.5, lng=127.0),
        "set_time",
        {"open_now": True, "night": True, "emergency": True},
    )
    assert state.target.open_now is True
    assert state.target.night_service is True
    assert state.target.emergency_service is True
    assert "set_time" not in tools.TOOLS


@pytest.mark.parametrize(("tool", "args"), [
    ("does_not_exist", {}),
    ("set_mode", {"mode": "bicycle"}),
    ("set_sort", {"sort": "random"}),
    ("set_walk_max_min", {"minutes": -1}),
    ("set_radius", {"m": 99_999_999}),
    ("set_origin", {"lat": 999, "lng": 127}),
    ("set_time_intent", {"kind": "service_at"}),
    ("set_mode", {"mode": "walk", "surprise": True}),
])
def test_bad_tool_calls_are_rejected(tool, args):
    """
    Contract: 모르는 툴·모르는 인자·범위 밖 값은 조용히 무시하지 않고 거부한다. UI 필터와
              자연어가 같은 툴로 들어오므로 이 경계가 둘 다를 지킨다.
    Decision: #35, #18
    """
    with pytest.raises(ToolInputError):
        tools.apply(EditableState(lat=37.5, lng=127.0), tool, args)
