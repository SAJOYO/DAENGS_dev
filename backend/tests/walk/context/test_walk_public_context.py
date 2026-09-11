"""Public provider boundaries; HTTP fixtures only, no keys or external requests."""

import json
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.services import walk_public_context as public
from daengs_backend.services.walk_entry_context_source import collect
from daengs_backend.services.walk_park_catalog import nearby_parks, read_catalog, refresh_catalog
from daengs_backend.services.walk_public_http import PublicSourceError, get_json
from daengs_backend.services.walk_sgis import SgisSource

POINT = {"lat": 37.5, "lng": 127.0}
DONG = {
    "sido_nm": "서울특별시",
    "sgg_nm": "강남구",
    "emdong_nm": "역삼1동",
    "sido_cd": "11",
    "sgg_cd": "230",
    "emdong_cd": "610",
}


def park(id="one", **updates):
    return {
        "manageNo": id,
        "parkNm": "합성 공원",
        "parkSe": "근린공원",
        "latitude": "37.5",
        "longitude": "127.001",
        "referenceDate": "2026-09-01",
        **updates,
    }


def page(items, total=None):
    return {
        "response": {
            "header": {"resultCode": "00"},
            "body": {
                "items": {"item": items},
                "totalCount": len(items) if total is None else total,
            },
        }
    }


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "walk_public_context_enabled", True)
    monkeypatch.setattr(settings, "walk_sgis_key", SecretStr("synthetic-key"))
    monkeypatch.setattr(settings, "walk_sgis_secret", SecretStr("synthetic-secret"))
    monkeypatch.setattr(public, "sgis", SgisSource())


async def test_address_uses_admin_dong_and_exact_coordinate_cache(configured, caplog):
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("authentication.json"):
            result = {"accessToken": "private-token", "accessTimeout": time.time() + 3600}
        elif request.url.path.endswith("transcoord.json"):
            assert request.url.params["src"] == "4326" and request.url.params["dst"] == "5179"
            result = {"posX": 900000, "posY": 1900000}
        else:
            assert request.url.params["addr_type"] == "20"
            result = [DONG]
        return httpx.Response(200, json={"errCd": 0, "result": result})

    transport = httpx.MockTransport(respond)
    with caplog.at_level(logging.INFO):
        first = await public.collect_public(
            "space.address",
            {**POINT, "captured_at": "2026-09-09T01:00:00Z", "accuracy_m": 5},
            transport=transport,
        )
        again = await public.collect_public("space.address", POINT, transport=transport)
        await public.collect_public(
            "space.address", {**POINT, "lat": 37.5000001}, transport=transport
        )
    assert first == again and first.status == "known" and len(calls) == 5
    assert first.payload["address"] == DONG and first.provider == "sgis"
    assert first.retrieved_at and first.payload["query_point"] == POINT
    assert all(
        secret not in caplog.text
        for secret in ["synthetic-key", "synthetic-secret", "private-token"]
    )


@pytest.mark.parametrize(
    "rows,expected",
    [([], "empty"), ([{}], "unavailable"), ([DONG, DONG], "unavailable"), ([None], "unavailable")],
)
async def test_empty_and_invalid_dong_are_distinct(configured, rows, expected):
    public.sgis.token = None
    results = iter(
        [
            {"accessToken": "token", "accessTimeout": time.time() + 1000},
            {"posX": 1, "posY": 2},
            rows,
        ]
    )
    response = await public.collect_public(
        "space.address",
        POINT,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"errCd": 0, "result": next(results)})
        ),
    )
    assert response.status == expected


@pytest.mark.parametrize("status,retryable", [(302, False), (401, False), (429, True), (503, True)])
async def test_provider_failure_has_safe_reason_and_no_query_string(status, retryable):
    with pytest.raises(PublicSourceError) as caught:
        await get_json(
            httpx.MockTransport(lambda _: httpx.Response(status)),
            "https://example.invalid",
            {"serviceKey": "secret"},
        )
    assert caught.value.reason == f"http_{status}" and caught.value.retryable == retryable
    assert "secret" not in str(caught.value)


async def test_response_size_and_non_json_are_bounded():
    with pytest.raises(PublicSourceError, match="response_too_large"):
        await get_json(
            httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 20)),
            "https://example.invalid",
            {},
            limit=10,
        )
    with pytest.raises(PublicSourceError, match="invalid_response"):
        await get_json(
            httpx.MockTransport(lambda _: httpx.Response(200, content=b"<html>")),
            "https://example.invalid",
            {},
        )


async def test_catalog_atomic_refresh_rejects_conflicts_and_preserves_previous_on_failure(tmp_path):
    path = tmp_path / "parks.json"
    rows = [
        park(),
        park(),
        park("conflict"),
        park("conflict", longitude="128"),
        park("bad", latitude="NaN"),
        park("far", longitude="128"),
    ]
    value = await refresh_catalog(
        httpx.MockTransport(lambda _: httpx.Response(200, json=page(rows))), "synthetic%3D", path
    )
    assert value["rejected_rows"] == 4
    assert value["rejected_reasons"] == {
        "duplicate_row": 1,
        "conflicting_identity": 2,
        "invalid_row": 1,
    }
    found, partial = nearby_parks(read_catalog(path), POINT)
    assert partial and [p["manageNo"] for p in found] == ["one"]
    assert 80 < found[0]["distance_m"] < 90
    before = path.read_bytes()
    replies = iter([page([park()], 2), page([], 2)])
    with pytest.raises(PublicSourceError, match="incomplete"):
        await refresh_catalog(
            httpx.MockTransport(lambda _: httpx.Response(200, json=next(replies))), "key", path
        )
    assert path.read_bytes() == before
    value["retrieved_at"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        read_catalog(path)


async def test_public_park_preserves_pin_uncertainty_and_cache_time(
    configured, monkeypatch, tmp_path
):
    path = tmp_path / "parks.json"
    value = await refresh_catalog(
        httpx.MockTransport(lambda _: httpx.Response(200, json=page([park()]))), "key", path
    )
    monkeypatch.setattr(settings, "walk_park_catalog_path", str(path))
    pin = {
        "state": "resolved",
        "point": POINT,
        "method": "last_known",
        "uncertainty_m": 60,
        "uncertainty_basis": "device_accuracy",
    }
    result = await collect("space.park", {"pin": pin})
    assert result.status == "known" and result.retrieved_at == value["retrieved_at"]
    assert (
        result.payload["geometry"] == "registered_point" and not result.payload["visit_confirmed"]
    )
    assert result.payload["uncertainty_m"] == 60
    empty = await public.collect_public("space.park", {"lat": 36, "lng": 127})
    assert empty.status == "empty"
    monkeypatch.setattr(settings, "walk_park_catalog_path", str(tmp_path / "absent.json"))
    assert (await collect("space.park", {"location": POINT})).status == "unavailable"
    assert (await collect("space.address", {})).reason == "no_location"
    assert (
        await collect("space.address", {"pin": {**pin, "state": "provisional"}})
    ).reason == "pin_not_final"
    monkeypatch.setattr(settings, "walk_public_context_enabled", False)
    assert (await collect("space.address", {"location": POINT})).status == "not_requested"
