"""Refresh due public snapshots outside entry leases, under one lock and a request budget."""

import asyncio
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
from redis.asyncio import Redis
from shapely.errors import ShapelyError

from daengs_backend.config import settings
from daengs_backend.repositories import walk_catalog_demand as demand
from daengs_backend.services.walk_background.catalogs import area as walk_area_catalog
from daengs_backend.services.walk_background.catalogs import commerce as walk_commerce_catalog
from daengs_backend.services.walk_background.catalogs import park as walk_park_catalog
from daengs_backend.services.walk_background.catalogs import regions
from daengs_backend.services.walk_background.catalogs import river as walk_river_catalog
from daengs_backend.services.walk_background.http import PublicSourceError

LOCK = "walk-public:refresh:lock:v1"
RELEASE = (
    "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0"
)
BUDGET = """
local n = tonumber(redis.call('GET',KEYS[1]) or '0')
if n >= tonumber(ARGV[1]) then return 0 end
n = redis.call('INCR',KEYS[1])
if n == 1 then redis.call('EXPIRE',KEYS[1],172800) end
return n
"""
ERRORS = (ValueError, KeyError, TypeError, OSError, OverflowError, ShapelyError)


class BudgetTransport(httpx.AsyncBaseTransport):
    """Count pages, failed requests and river metadata too. Never log request URLs."""

    def __init__(self, opened, broker):
        self.opened, self.broker, self.requests = opened, broker, 0

    async def handle_async_request(self, request):
        if self.requests >= 80:
            raise PublicSourceError("catalog_cycle_budget")
        day = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
        if not await self.broker.eval(
            BUDGET, 1, "walk-public:requests:" + day, settings.walk_catalog_daily_requests
        ):
            raise PublicSourceError("catalog_daily_budget")
        self.requests += 1
        return await self.opened.handle_async_request(request)


def fresh(path, kind, center=None):
    try:
        value = (
            walk_park_catalog.read_catalog(path)
            if kind == "park"
            else walk_area_catalog.read(path, kind)
        )
        age = (datetime.now(UTC) - datetime.fromisoformat(value["retrieved_at"])).total_seconds()
        if kind != "park":
            if center is not None and value["area"] != walk_area_catalog.area(
                center, regions.RADIUS_M
            ):
                return False
            source = walk_commerce_catalog if kind == "commerce" else walk_river_catalog
            source.nearby(value, value["area"]["center"])
        return age < regions.REFRESH_DAYS * 86400
    except (*ERRORS, PublicSourceError):
        return False


def ready(item):
    """Use the same snapshot and complete query-window checks as the actual collector."""
    try:
        if item["tag"] == "space.park":
            value = walk_park_catalog.read_catalog(settings.walk_park_catalog_path)
            walk_park_catalog.nearby_parks(value, item["point"])
        else:
            kind = item["tag"].removeprefix("space.")
            value = regions.select(kind, item["point"])
            source = walk_commerce_catalog if kind == "commerce" else walk_river_catalog
            source.nearby(value, item["point"])
        return True
    except (*ERRORS, PublicSourceError):
        return False


async def cycle(factory, broker, transport):
    async with factory() as session:
        items = await demand.pending_and_recent(session, datetime.now(UTC))
    # Transactions are closed before any file/provider I/O. No raw trajectory is required.
    groups = OrderedDict()
    for item in items:
        try:
            key, center = regions.region(item["point"])
        except ERRORS:
            continue
        groups.setdefault(key, (center, []))[1].append(item)
    report = {"regions_seen": len(groups), "refreshed": 0, "failed": 0, "woken": 0}
    public_key = settings.walk_public_data_key.get_secret_value().strip()
    standard = None

    async def update(key, path, kind, fetch, center=None):
        cooldown = "walk-public:cooldown:" + key
        if await asyncio.to_thread(fresh, path, kind, center) or await broker.exists(cooldown):
            return
        # Count attempts, not successes: a failing provider must not fan out indefinitely.
        if report["refreshed"] + report["failed"] >= 5:
            return
        try:
            await fetch()
            await broker.delete(cooldown)
            report["refreshed"] += 1
        except (*ERRORS, PublicSourceError):
            report["failed"] += 1
            await broker.set(cooldown, "1", ex=3600)
            # Source refresh only publishes complete snapshots; usable previous bytes survive.

    await update(
        "national-park",
        settings.walk_park_catalog_path,
        "park",
        lambda: walk_park_catalog.refresh_catalog(
            transport, public_key, settings.walk_park_catalog_path
        ),
    )
    for key, (center, _) in groups.items():
        for kind in ("commerce", "river"):
            path = regions.path_for(kind, center)

            async def fetch(kind=kind, path=path, center=center):
                nonlocal standard
                if kind == "commerce":
                    await walk_commerce_catalog.refresh(
                        transport, public_key, path, center, regions.RADIUS_M
                    )
                else:
                    # The national metadata request is shared by regions within this cycle.
                    if standard is None:
                        standard = await walk_river_catalog.standard_metadata(transport, public_key)
                    await walk_river_catalog.refresh(
                        transport, public_key, path, center, regions.RADIUS_M, standard=standard
                    )

            await update(key + ":" + kind, path, kind, fetch, center)
    pending = [i for i in items if i["state"] == "pending"]
    ready_ids = []
    for item in pending:
        if await asyncio.to_thread(ready, item):
            ready_ids.append(item["id"])
    async with factory() as session:
        report["woken"] = await demand.wake_ready(session, ready_ids, datetime.now(UTC))
        await session.commit()
    report["requests"] = transport.requests
    return report


async def run(factory):
    if not settings.walk_catalog_refresh_enabled:
        return {"status": "disabled"}
    if not regions.automatic_ready():
        return {"status": "not_configured"}
    try:
        async with Redis.from_url(
            settings.redis_url, socket_connect_timeout=5, socket_timeout=5
        ) as broker:
            token = uuid.uuid4().hex
            if not await broker.set(LOCK, token, nx=True, ex=300):
                return {"status": "busy"}
            try:
                async with asyncio.timeout(240), httpx.AsyncHTTPTransport() as opened:
                    return {
                        "status": "finished",
                        **await cycle(factory, broker, BudgetTransport(opened, broker)),
                    }
            finally:
                await broker.eval(RELEASE, 1, LOCK, token)
    except Exception as exc:  # noqa: BLE001 - never print URLs, key values, SQL or source payloads
        return {"status": "unavailable", "error_type": type(exc).__name__}
