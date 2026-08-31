"""Place v2 HTTP 표면의 입력 검증과 노출 경계 — 잘못된 입력이 500 이 되지 않게.

geo `tests/api/test_input_validation.py` 의 Place 부분만 `daengs_place.main` 기준으로
이식했다 (UPSTREAM.md). journey/static-map 검증은 geo 에 남는다 — 그 표면은 이관
대상이 아니다.
"""

import pytest
from fastapi.testclient import TestClient

from daengs_place.core.db import get_session
from daengs_place.main import app


async def _no_db():
    yield None


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
    # identity(dog_id)는 계약에 없고 값도 선택적이다. 프로필 저장소는 필요 없다.
    assert set(schema["PlaceSearchConditions"]["properties"]) == {
        "dog_size", "dog_weight_kg", "dog_age_years",
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
    assert (
        "conditions require at least one of dog_size, dog_weight_kg, dog_age_years"
        in response.text
    )


@pytest.mark.parametrize("extra_key", ["dog_id", "dog_weigth_kg"])
def test_v2_place_search_rejects_unknown_condition_keys(extra_key):
    """옛 계약(dog_id)이나 오타를 조용히 무시하면 덜 개인화된 결과가 정상처럼 나간다.

    `preferences` 가 미지원 키를 422 로 거부하는 것과 같은 이유다 (geo 결정 #73).
    """
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            response = client.post("/v2/places/search", json={
                "lat": 37.5,
                "lng": 127.0,
                "kinds": ["cafe"],
                "conditions": {"dog_size": "large", extra_key: "janggun"},
            })
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422
    assert extra_key in response.text
