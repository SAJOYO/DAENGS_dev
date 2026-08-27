"""HTTP 표면의 입력 검증과 노출 경계 — 잘못된 입력이 500 이나 외부 호출이 되지 않게.

이 파일이 깨지는 이유는 하나다: **HTTP 경계의 거부 규칙이나 응답에 나가는 값이 바뀌었다.**
state 자체의 해석은 `tests/discovery/test_state_contract.py` 가 지킨다.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.core.db import get_session
from app.discovery.state import EditableState, JourneyPrefs
from app.features.hospital.api import HospitalSearchIn
from app.features.journey.api import Dest, JourneyIn
from app.main import app


def test_api_input_models_reject_invalid_coordinates_and_unbounded_lists():
    """
    Contract: 좌표 범위와 리스트 길이는 입력 모델에서 막는다. 목적지 없는 요청,
              origin 과 state 를 동시에 주는 모순 조합도 거부한다.
    Decision: #35
    """
    with pytest.raises(ValidationError):
        HospitalSearchIn(origin=(999, -999))
    with pytest.raises(ValidationError):
        HospitalSearchIn()
    with pytest.raises(ValidationError):
        HospitalSearchIn(origin=(37.5, 127.0), shown_ids=list(range(1, 102)))
    with pytest.raises(ValidationError):
        JourneyIn(origin=(999, -999), dests=[Dest(lat=37.5, lng=127.0)])
    with pytest.raises(ValidationError):
        JourneyIn(
            origin=(37.5, 127.0),
            dests=[Dest(lat=37.5, lng=127.0)],
            state=EditableState(lat=37.5, lng=127.0),
            prefs=JourneyPrefs(),
        )
    with pytest.raises(ValidationError):
        Dest(name="missing coordinates")


async def _no_db():
    yield None


def test_bad_edit_is_an_http_422_not_a_500():
    """
    Contract: 나쁜 edit 은 422 이고 어느 툴이 문제인지 말해준다. 500 이면 클라이언트가
              자기 잘못인지 서버 장애인지 구분하지 못한다.
    Decision: #35
    """
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            response = client.post("/hospital/search", json={
                "origin": [37.5, 127.0],
                "transport": "none",
                "edits": [{"tool": "set_mode", "args": {"mode": "bicycle"}}],
            })
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422
    assert "invalid args for set_mode" in response.json()["detail"]


def test_static_map_rejects_bad_query_before_provider_call():
    """
    Contract: 잘못된 쿼리는 제공사에 나가기 **전에** 막는다. 유료 호출을 잘못된 입력에
              쓰지 않는다.
    Decision: #35, #47
    """
    with TestClient(app) as client:
        bad_origin = client.get("/map/static?lat=999&lng=127")
        bad_marker = client.get("/map/static?lat=37.5&lng=127&m=999:127:A:0")
    assert bad_origin.status_code == 422
    assert bad_marker.status_code == 422


def test_facility_search_rejects_the_retired_goods_kind():
    """폐기된 분류를 빈 결과로 돌려주면 데이터가 없는 것으로 오독한다."""
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            response = client.get(
                "/facility/search?lat=37.5&lng=127&radius_m=3000&kind=goods"
            )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422
    assert "goods was split into pet_shop and shopping" in response.text


@pytest.mark.parametrize(
    ("kinds", "message"),
    [
        ([], "at least 1 item"),
        (["goods"], "goods was split into pet_shop and shopping"),
        (["not-a-kind"], "unknown place kinds"),
        (["cafe", "cafe"], "kinds must be unique"),
    ],
)
def test_v2_place_search_requires_explicit_canonical_kinds(kinds, message):
    """후보군을 고르지 않거나 폐기된 분류를 보내면 DB를 읽기 전에 거부한다."""
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            response = client.post("/v2/places/search", json={
                "lat": 37.5,
                "lng": 127.0,
                "radius_m": 3000,
                "kinds": kinds,
            })
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422
    assert message in response.text


@pytest.mark.parametrize(
    "body",
    [
        {
            "kinds": [
                "hospital", "pharmacy", "cafe", "travel", "shopping", "pet_shop",
                "grooming",
            ],
        },
        {"kinds": ["hospital", "cafe"], "limit_per_kind": 3000},
    ],
)
def test_v2_place_search_enforces_a_whole_request_budget(body):
    """그룹별 상한을 곱해 한 HTTP 요청의 DB 작업·응답 크기 경계를 뚫을 수 없다."""
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            response = client.post("/v2/places/search", json={
                "lat": 37.5,
                "lng": 127.0,
                "radius_m": 3000,
                **body,
            })
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422


def test_v2_openapi_exposes_the_shared_place_kind_vocabulary():
    """클라이언트가 별도 하드코딩 없이 같은 canonical kind enum을 생성할 수 있어야 한다."""
    schema = app.openapi()["components"]["schemas"]

    assert {"hospital", "pharmacy", "pet_shop", "shopping"} <= set(
        schema["PlaceKind"]["enum"]
    )
    assert "goods" not in schema["PlaceKind"]["enum"]
    request_schema = schema["PlaceSearchRequest"]
    assert request_schema["properties"]["kinds"]["maxItems"] == 6
    assert "conditions" in request_schema["properties"]
    assert "preferences" in request_schema["properties"]
    assert set(schema["PlaceSearchConditions"]["properties"]) == {
        "dog_id", "dog_size", "dog_weight_kg",
    }
    assert set(schema["PlaceSearchPreferences"]["properties"]) == {"parking"}


def test_v2_place_search_rejects_an_unsupported_preference_before_reading_the_db():
    """아직 정의하지 않은 선호를 조용히 무시하면 UI와 실제 정렬이 달라진다."""
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            response = client.post("/v2/places/search", json={
                "lat": 37.5,
                "lng": 127.0,
                "kinds": ["cafe"],
                "preferences": {"open_now": True},
            })
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422
    assert "Extra inputs are not permitted" in response.text


def test_v2_place_search_rejects_empty_dog_conditions_before_reading_the_db():
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            response = client.post("/v2/places/search", json={
                "lat": 37.5,
                "lng": 127.0,
                "kinds": ["cafe"],
                "conditions": {},
            })
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422
    assert "conditions require dog_id or dog_size" in response.text


def test_map_client_config_exposes_only_browser_key_id(monkeypatch):
    """
    Contract: 브라우저가 받는 설정에는 key id 만 나가고 서버 secret 은 응답 어디에도
              없다. 정적 지도를 서버 프록시로 둔 이유가 이 secret 보관이다.
    Decision: #13
    """
    monkeypatch.setattr(settings, "map_provider", "naver")
    monkeypatch.setattr(settings, "naver_ncp_key_id", "public-key-id")
    monkeypatch.setattr(settings, "naver_ncp_key", "server-secret")

    with TestClient(app) as client:
        response = client.get("/map/client-config")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "naver",
        "naver_key_id": "public-key-id",
        "fallback": "osm",
    }
    assert "server-secret" not in response.text
