"""`POST /weather/at` 공개 계약과 저하 응답."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daengs_life.app.deps import get_cache, get_now
from daengs_life.app.main import create_app
from daengs_life.realtime.cache import Cache, MemoryStore
from daengs_life.realtime.config import KST
from daengs_life.realtime.providers import kma_vilage_fcst
from daengs_life.realtime.transport.base import Unavailable

FIXTURES = Path(__file__).parent / "fixtures" / "realtime"
NOW = datetime(2026, 8, 25, 10, 10, tzinfo=KST)
REQUEST = {
    "lat": 37.4979,
    "lon": 127.0276,
    "observed_at": "2026-08-25T09:20:00+09:00",
}


def ncst() -> dict:
    payload = json.loads((FIXTURES / "kma-vilage-fcst.ncst.json").read_text(encoding="utf-8"))
    return payload["response"]["body"]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(kma_vilage_fcst, "raw_ncst_at", lambda *_a, **_k: ncst())
    app = create_app()
    cache = Cache(MemoryStore())
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def test_response_preserves_values_representations_and_provenance(client: TestClient) -> None:
    response = client.post("/weather/at", json=REQUEST)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "captured"
    assert data["grid"] == [61, 125]
    assert data["requested_at"] == "2026-08-25T09:20:00+09:00"
    assert data["fetched_at"] == "2026-08-25T10:10:00+09:00"
    assert data["sources"] == [
        {
            "provider": "kma-vilage-fcst:ncst",
            "outcome": "ok",
            "reason": None,
            "calls": 1,
        }
    ]

    atoms = {atom["quantity"]: atom for atom in data["observations"]}
    assert atoms["temp_c"]["representation"] == "number"
    assert atoms["temp_c"]["unit"] == "celsius"
    assert atoms["precip_kind"]["representation"] == "code"
    assert atoms["precip_kind"]["raw_value"] == "0"
    assert atoms["precip_mm"]["representation"] == "interval"
    assert atoms["precip_mm"]["lower_bound"] == atoms["precip_mm"]["upper_bound"] == 0.0
    assert {atom["source"] for atom in atoms.values()} == {"kma-vilage-fcst:ncst"}


@pytest.mark.parametrize(
    "change",
    [
        {"lat": 0.0},
        {"lon": 0.0},
        {"observed_at": "2026-08-25T09:20:00"},
        {"unexpected": True},
    ],
)
def test_invalid_public_input_is_422(client: TestClient, change: dict) -> None:
    assert client.post("/weather/at", json={**REQUEST, **change}).status_code == 422


def test_future_time_is_422(client: TestClient) -> None:
    body = {**REQUEST, "observed_at": "2026-08-25T10:11:00+09:00"}
    response = client.post("/weather/at", json=body)

    assert response.status_code == 422
    assert "현재보다 늦을 수 없습니다" in response.json()["detail"]


def test_provider_failure_is_a_200_with_an_explicit_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def dead(*_a, **_k):
        raise Unavailable("게이트웨이 장애")

    monkeypatch.setattr(kma_vilage_fcst, "raw_ncst_at", dead)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: Cache(MemoryStore())
    app.dependency_overrides[get_now] = lambda: NOW

    response = TestClient(app).post("/weather/at", json=REQUEST)

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["sources"][0]["outcome"] == "error"
    assert response.json()["observations"] == []
