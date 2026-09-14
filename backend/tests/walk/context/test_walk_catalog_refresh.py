"""Offline boundary checks using actual public readers/publishers and fake provider replies."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.services import walk_area_catalog as catalog
from daengs_backend.services import walk_catalog_refresh as refresh
from daengs_backend.services import walk_catalog_regions as regions
from daengs_backend.services.walk_public_context import collect_public
from daengs_backend.services.walk_public_http import PublicSourceError, get_json
from daengs_walk.diary.contracts.input import digest

POINTS = [{"lat": 37.5, "lng": 127.0}, {"lat": 37.66, "lng": 126.754}]


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    for field in (
        "walk_entry_context_enabled",
        "walk_public_context_enabled",
        "walk_area_context_enabled",
        "walk_entry_v2_enabled",
        "walk_catalog_refresh_enabled",
    ):
        monkeypatch.setattr(settings, field, True)
    monkeypatch.setattr(settings, "walk_public_catalog_root", str(tmp_path / "regions"))
    monkeypatch.setattr(settings, "walk_park_catalog_path", str(tmp_path / "parks.json"))
    monkeypatch.setattr(settings, "walk_commerce_catalog_path", "")
    monkeypatch.setattr(settings, "walk_river_catalog_path", "")
    monkeypatch.setattr(settings, "walk_public_data_key", SecretStr("private-key"))
    monkeypatch.setattr(settings, "redis_url", "redis://localhost/15")
    return tmp_path


def park(path):
    rows = [
        {
            "manageNo": "park1",
            "parkNm": "공원",
            "parkSe": "근린공원",
            "referenceDate": "2026-01-01",
            "latitude": 37.5,
            "longitude": 127.0,
        }
    ]
    path.write_text(
        json.dumps(
            {
                "format": "public-park-catalog-v1",
                "retrieved_at": datetime.now(UTC).isoformat(),
                "total": 1,
                "rejected_rows": 0,
                "parks": rows,
                "parks_sha256": digest(rows),
            }
        ),
        encoding="utf-8",
    )


def regional(point, kind="commerce", *, days=0):
    key, center = regions.region(point)
    extra = {}
    if kind == "river":
        extra = {
            "geometry_crs": "EPSG:5179",
            "standard": {"rows": [], "rejected_rows": 0, "status": "empty", "reason": None},
        }
    value = catalog.publish(
        regions.path_for(kind, point), kind, catalog.area(center, 1200), [], extra=extra
    )
    if days:
        value["retrieved_at"] = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        value.pop("sha256")
        value["sha256"] = digest(value)
        regions.path_for(kind, point).write_text(json.dumps(value), encoding="utf-8")
    return key, value


def test_region_corners_cover_entire_query_and_have_stable_identity(enabled):
    key, center = regions.region(POINTS[0])
    x, y = catalog.xy(center)
    for dx in (-499, 0, 499):
        for dy in (-499, 0, 499):
            lng, lat = catalog.REVERSE.transform(x + dx, y + dy)
            point = {"lat": lat, "lng": lng}
            assert regions.region(point)[0] == key
            assert catalog.covers({"area": catalog.area(center, 1200)}, point, 250)
    assert regions.region(POINTS[1])[0] != key
    assert not regions.can_prepare({"lat": float("nan"), "lng": 127.0})


def test_recent_but_wrong_region_catalog_needs_repair(enabled):
    _, correct = regions.region(POINTS[0])
    _, other = regions.region(POINTS[1])
    path = regions.path_for("commerce", POINTS[0])
    catalog.publish(path, "commerce", catalog.area(other, 1200), [])
    assert not refresh.fresh(path, "commerce", correct)


async def test_two_regions_remain_separate_and_never_report_missing_area_as_empty(enabled):
    for point in POINTS:
        regional(point)
    paths = [regions.path_for("commerce", p) for p in POINTS]
    originals = [p.read_bytes() for p in paths]
    for point in POINTS:
        value = await collect_public("space.commerce", point)
        assert value.status == "empty"
        assert value.payload["catalog_area"]["center"] == regions.region(point)[1]
    assert [p.read_bytes() for p in paths] == originals
    missing = await collect_public("space.commerce", {"lat": 35.15, "lng": 129.06})
    assert (
        missing.status == "unavailable"
        and missing.reason == "catalog_preparing"
        and missing.retryable
    )


async def test_refresh_age_does_not_change_hard_expiry_or_legacy_fallback(enabled, monkeypatch):
    regional(POINTS[0], days=21)
    path = regions.path_for("commerce", POINTS[0])
    assert not refresh.fresh(path, "commerce")
    assert (await collect_public("space.commerce", POINTS[0])).status == "empty"
    regional(POINTS[0], days=31)
    assert (await collect_public("space.commerce", POINTS[0])).reason == "catalog_preparing"
    legacy = enabled / "legacy.json"
    catalog.publish(legacy, "commerce", catalog.area(POINTS[0], 1200), [])
    monkeypatch.setattr(settings, "walk_commerce_catalog_path", str(legacy))
    assert (await collect_public("space.commerce", POINTS[0])).status == "empty"
    monkeypatch.setattr(settings, "walk_catalog_refresh_enabled", False)
    assert (await collect_public("space.commerce", POINTS[1])).reason == "outside_catalog_coverage"


class Broker:
    def __init__(self):
        self.values = {}
        self.count = 0
        self.__aenter__ = AsyncMock(return_value=self)

    async def exists(self, key):
        return key in self.values

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        self.values.pop(key, None)

    async def eval(self, script, count, key, arg):
        if script == refresh.RELEASE:
            if self.values.get(key) == arg:
                await self.delete(key)
            return 0
        if self.count >= int(arg):
            return 0
        self.count += 1
        return self.count


def factory(monkeypatch, items):
    db = AsyncMock()
    session = AsyncMock()
    session.__aenter__.return_value = db
    monkeypatch.setattr(refresh.demand, "pending_and_recent", AsyncMock(return_value=items))
    wake = AsyncMock(side_effect=lambda _session, ids, _now: len(ids))
    monkeypatch.setattr(refresh.demand, "wake_ready", wake)
    return lambda: session, wake


async def test_cycle_downloads_both_regions_once_and_wakes_matching_pending(enabled, monkeypatch):
    park(enabled / "parks.json")
    items = [
        {"id": f"{i}:{kind}", "tag": "space." + kind, "state": "pending", "point": point}
        for i, point in enumerate(POINTS)
        for kind in ("commerce", "river")
    ]
    session, wake = factory(monkeypatch, items)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if "geoserver" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "type": "FeatureCollection",
                    "features": [],
                    "crs": {"properties": {"name": "EPSG:3857"}},
                    "numberMatched": 0,
                    "numberReturned": 0,
                },
            )
        return httpx.Response(
            200,
            json={
                "response": {"header": {"resultCode": "03"}, "body": {"items": [], "totalCount": 0}}
            },
        )

    broker = Broker()
    transport = refresh.BudgetTransport(httpx.MockTransport(respond), broker)
    first = await refresh.cycle(session, broker, transport)
    assert first["refreshed"] == 4 and first["failed"] == 0 and first["woken"] == 4
    assert len(calls) == 5  # two commerce, two geometry, one shared national metadata request
    assert len(wake.call_args.args[1]) == 4
    for point in POINTS:
        assert regions.select("commerce", point)["area"]["center"] == regions.region(point)[1]
        assert regions.select("river", point)["area"]["center"] == regions.region(point)[1]
    again = await refresh.cycle(session, broker, transport)
    assert again["refreshed"] == 0 and len(calls) == 5


async def test_provider_failure_preserves_usable_old_bytes_and_backs_off(enabled, monkeypatch):
    park(enabled / "parks.json")
    for kind in ("commerce", "river"):
        regional(POINTS[0], kind, days=21)
    paths = [regions.path_for(k, POINTS[0]) for k in ("commerce", "river")]
    before = [p.read_bytes() for p in paths]
    session, _ = factory(
        monkeypatch,
        [{"id": "job", "tag": "space.commerce", "state": "completed", "point": POINTS[0]}],
    )
    broker = Broker()
    transport = refresh.BudgetTransport(httpx.MockTransport(lambda _: httpx.Response(503)), broker)
    result = await refresh.cycle(session, broker, transport)
    assert result["failed"] == 2 and result["refreshed"] == 0
    assert [p.read_bytes() for p in paths] == before
    used = broker.count
    await refresh.cycle(session, broker, transport)
    assert broker.count == used
    assert (await collect_public("space.commerce", POINTS[0])).status == "empty"


async def test_budget_counts_failed_requests_before_network_and_never_leaks_key(
    enabled, monkeypatch
):
    monkeypatch.setattr(settings, "walk_catalog_daily_requests", 1)
    broker = Broker()
    sent = []
    transport = refresh.BudgetTransport(
        httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(503)), broker
    )
    with pytest.raises(PublicSourceError, match="http_503"):
        await get_json(transport, "https://example.test", {"key": "private-key"})
    with pytest.raises(PublicSourceError, match="catalog_daily_budget") as caught:
        await get_json(transport, "https://example.test", {"key": "private-key"})
    assert len(sent) == 1 and "private-key" not in str(caught.value)


async def test_duplicate_runner_skips_and_lock_is_released_on_error(enabled, monkeypatch):
    broker = AsyncMock()
    broker.__aenter__.return_value = broker
    broker.set.return_value = False
    monkeypatch.setattr(refresh.Redis, "from_url", lambda *args, **kwargs: broker)
    execute = AsyncMock(side_effect=RuntimeError("private-key"))
    monkeypatch.setattr(refresh, "cycle", execute)
    assert (await refresh.run(None))["status"] == "busy"
    execute.assert_not_called()
    broker.set.return_value = True
    failed = await refresh.run(None)
    assert failed == {"status": "unavailable", "error_type": "RuntimeError"}
    broker.eval.assert_awaited_once()
    assert broker.eval.call_args.args[0] == refresh.RELEASE


def test_catalog_task_has_separate_queue_and_expiry():
    from daengs_backend.tasks.walk_entry_context import app

    scheduled = app.conf.beat_schedule["public-catalog-refresh"]
    assert scheduled["options"] == {"queue": "walk-public-catalog", "expires": 55}
    assert app.conf.task_default_queue == "walk-entry-context"
