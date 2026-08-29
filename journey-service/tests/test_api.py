from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def app_payload(**overrides) -> dict:
    payload = {
        "origin": [37.4979, 127.0276],
        "dests": [{"lat": 37.5145, "lng": 127.0316, "name": "댕스동물병원"}],
        "companion": "dog",
        "measured": True,
        "with_polyline": False,
    }
    payload.update(overrides)
    return payload


def stable_transport(payload: dict) -> dict:
    response = client.post("/journey", json=payload)
    assert response.status_code == 200, response.text
    transport = response.json()["items"][0]["transport"]
    transport.pop("as_of")
    return transport


def test_current_app_request_keeps_original_profile_free_contract() -> None:
    response = client.post("/journey", json=app_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["companion"] == "dog"
    item = body["items"][0]
    assert item["id"] is None
    assert item["lat"] == 37.5145
    assert item["lng"] == 127.0316
    transport = item["transport"]
    assert transport["mode_priority"] == ["walk", "car", "transit"]
    assert transport["walk"]["status"] == "estimate"
    assert transport["walk"]["status_reason"] == "provider_is_fake"
    assert transport["walk"]["provider_min"] is not None
    assert transport["walk"]["polyline"] is None
    assert transport["walk"]["handoff"]["naver"].startswith("nmap://route/walk")
    assert transport["car"]["handoff"]["kakao"].endswith("by=CAR")
    assert transport["transit"]["handoff"]["tmap"].startswith("tmap://route")


def test_non_empty_dog_id_is_rejected_instead_of_silently_ignored() -> None:
    response = client.post("/journey", json=app_payload(dog_id="unknown-dog"))

    assert response.status_code == 422
    assert "dog_id requires a profile source" in response.text


def test_blank_dog_id_uses_the_same_profile_free_path_as_an_absent_id() -> None:
    without_id = stable_transport(app_payload())
    blank_id = stable_transport(app_payload(dog_id="  "))

    assert blank_id == without_id


def test_internal_place_id_is_not_accepted_by_the_coordinate_contract() -> None:
    id_response = client.post(
        "/journey",
        json=app_payload(dests=[{"id": 7, "lat": 37.5145, "lng": 127.0316}]),
    )

    assert id_response.status_code == 422


def test_legacy_prefs_restore_the_original_mode_priority() -> None:
    response = client.post(
        "/journey",
        json=app_payload(prefs={"preferred_mode": "transit"}, measured=False),
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["transport"]["mode_priority"][0] == "transit"


def test_state_carries_urgency_and_walk_limit_but_request_origin_wins() -> None:
    response = client.post(
        "/journey",
        json=app_payload(
            state={
                "state_version": 4,
                "lat": 0,
                "lng": 0,
                "urgency": "urgent",
                "journey": {
                    "preferred_mode": "walk",
                    "walk": {"max_walk_min": 1},
                },
            },
            measured=False,
        ),
    )

    assert response.status_code == 200
    transport = response.json()["items"][0]["transport"]
    assert transport["mode_priority"][0] == "car"
    assert transport["walk"]["advice"] == "avoid"
    assert transport["straight_m"] < 10_000


def test_state_and_prefs_cannot_be_sent_together() -> None:
    response = client.post(
        "/journey",
        json=app_payload(
            state={"state_version": 4, "lat": 37.5, "lng": 127.0},
            prefs={"preferred_mode": "walk"},
        ),
    )

    assert response.status_code == 422


def test_coordinates_and_destination_count_are_validated() -> None:
    invalid_coordinate = client.post(
        "/journey",
        json=app_payload(dests=[{"lat": 91, "lng": 127.0, "name": "invalid"}]),
    )
    no_destination = client.post("/journey", json=app_payload(dests=[]))

    assert invalid_coordinate.status_code == 422
    assert no_destination.status_code == 422


def test_health_reports_route_configuration() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "walk_route_provider": "fake",
        "usage_policy": "deny-all",
    }
