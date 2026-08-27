"""실제 유료 provider는 허용·요청 한도·누적 사용량 영수증 없이 호출할 수 없다."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_session
from app.discovery.refine.nl import MeteredLLM, ToolCall
from app.discovery.state import EditableState
from app.journey import engine
from app.main import app
from app.providers import registry as provider_registry
from app.providers.base import LatLng, RouteResult, StaticMapSpec
from app.usage import composition as usage_composition
from app.usage.gate import UsageGate, usage_request_scope
from app.usage.ledger import InMemoryLedger
from app.usage.metered import MeteredRouteProvider, MeteredStaticMapFetcher
from app.usage.models import MeasuredRouteIntent, UsageDenied
from app.usage.policy import (
    BoundedDevPolicy,
    DenyAllPolicy,
    DevUsageLimits,
    OperationLimit,
)
from app.usage.registry import usage_gate


class SpyRouteProvider:
    name = "spy-route"
    route_modes = frozenset({"walk"})

    def __init__(self):
        self.calls = 0

    def static_map_url(self, spec):
        return None

    async def geocode(self, address):
        return None

    async def reverse_geocode(self, pos):
        return None

    async def route(self, mode, origin, dest):
        self.calls += 1
        return RouteResult(
            mode=mode,
            distance_m=100,
            duration_s=60,
            source=self.name,
        )


class SpyStaticMapFetcher:
    def __init__(self):
        self.calls = 0

    async def fetch_static_png(self, spec):
        self.calls += 1
        return b"png"


class SpyLLM:
    def __init__(self):
        self.calls = 0

    async def plan(self, utterance, state, shown_ids, profile_hint):
        self.calls += 1
        return [ToolCall("ask", {"question": "q"})]


def deny_gate() -> UsageGate:
    return UsageGate(DenyAllPolicy(), InMemoryLedger())


def small_dev_gate(*, request_units: int = 2, window_units: int = 3) -> UsageGate:
    limits = DevUsageLimits(
        static_map=OperationLimit(request_units, window_units),
        measured_route=OperationLimit(request_units, window_units),
        language_parse=OperationLimit(request_units, window_units),
    )
    return UsageGate(BoundedDevPolicy(limits), InMemoryLedger())


async def test_default_policy_denies_all_three_real_call_edges():
    route_spy = SpyRouteProvider()
    static_spy = SpyStaticMapFetcher()
    llm_spy = SpyLLM()
    gate = deny_gate()
    route = MeteredRouteProvider(route_spy, gate)
    static = MeteredStaticMapFetcher(static_spy, gate)
    llm = MeteredLLM(llm_spy, gate)
    state = EditableState(lat=37.5, lng=127.0)

    async with usage_request_scope():
        with pytest.raises(UsageDenied, match="not configured"):
            await route.route("walk", LatLng(37.5, 127.0), LatLng(37.51, 127.01))
        with pytest.raises(UsageDenied, match="not configured"):
            await static.fetch_static_png(StaticMapSpec(LatLng(37.5, 127.0)))
        with pytest.raises(UsageDenied, match="not configured"):
            await llm.plan("가까운 곳", state, [], "없음")

    assert (route_spy.calls, static_spy.calls, llm_spy.calls) == (0, 0, 0)


async def test_dev_policy_enforces_request_and_window_limits_without_refund():
    gate = small_dev_gate(request_units=2, window_units=3)
    intent = MeasuredRouteIntent(mode="walk")

    async with usage_request_scope():
        for _ in range(2):
            permit = await gate.check(intent)
            await gate.consume(intent, permit)
        permit = await gate.check(intent)          # 허용 여부는 통과 — 세는 건 consume 이다
        with pytest.raises(UsageDenied) as request_denied:
            await gate.consume(intent, permit)
    assert request_denied.value.code == "request_limit"

    async with usage_request_scope():
        permit = await gate.check(intent)
        await gate.consume(intent, permit)  # 누적 3 — 마지막 허용
        permit = await gate.check(intent)
        with pytest.raises(UsageDenied) as usage_denied:
            await gate.consume(intent, permit)
    assert usage_denied.value.code == "usage_limit"


async def test_route_cache_hit_consumes_neither_request_nor_window_units():
    """hit 는 상류 호출이 아니다. 요청당 한도도 누적 장부도 올리면 안 된다."""
    spy = SpyRouteProvider()
    provider = MeteredRouteProvider(spy, small_dev_gate(request_units=1, window_units=1))
    origin, dest = LatLng(37.5, 127.0), LatLng(37.51, 127.01)

    async with usage_request_scope():
        first = await provider.route("walk", origin, dest)          # miss — 요청 1, 누적 1
        second = await provider.route("walk", origin, dest)         # hit — 한도 1 인데 통과해야 한다
        with pytest.raises(UsageDenied) as denied:
            await provider.route("walk", origin, LatLng(37.52, 127.02))   # miss — 요청 한도 초과

    assert first is second
    assert denied.value.code == "request_limit"
    assert spy.calls == 1


async def test_route_denial_degrades_to_labelled_estimate_without_call(monkeypatch):
    spy = SpyRouteProvider()
    provider = MeteredRouteProvider(spy, deny_gate())
    monkeypatch.setattr(engine, "route_provider_name", lambda mode: "tmap")
    monkeypatch.setattr(engine, "route_provider", lambda mode: provider)

    async with usage_request_scope():
        outcome = await engine._route(
            "walk", LatLng(37.5, 127.0), LatLng(37.51, 127.01), measured=True
        )

    assert outcome.status == "estimate" and outcome.reason == "usage_denied"
    assert spy.calls == 0


def test_static_map_denial_is_http_403_and_never_calls_provider(monkeypatch):
    from app.api import static_map as static_map_api

    spy = SpyStaticMapFetcher()
    fetcher = MeteredStaticMapFetcher(spy, deny_gate())
    monkeypatch.setattr(static_map_api, "static_map_fetcher", lambda: fetcher)

    with TestClient(app) as client:
        response = client.get("/map/static?lat=37.5&lng=127")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "policy_denied"
    assert spy.calls == 0


def test_static_map_dev_limit_returns_429_and_success_is_cacheable(monkeypatch):
    from app.api import static_map as static_map_api

    spy = SpyStaticMapFetcher()
    fetcher = MeteredStaticMapFetcher(spy, small_dev_gate(request_units=1, window_units=1))
    monkeypatch.setattr(static_map_api, "static_map_fetcher", lambda: fetcher)

    with TestClient(app) as client:
        allowed = client.get("/map/static?lat=37.5&lng=127")
        denied = client.get("/map/static?lat=37.5&lng=127")

    assert allowed.status_code == 200 and allowed.content == b"png"
    assert allowed.headers["cache-control"] == "public, max-age=86400"
    assert denied.status_code == 429
    assert denied.json()["detail"]["code"] == "usage_limit"
    assert spy.calls == 1


async def _no_db():
    yield None


def test_llm_denial_is_explicit_http_403_not_silent_fake_fallback(monkeypatch):
    from app.discovery.refine import engine as refine_engine

    spy = SpyLLM()
    metered = MeteredLLM(spy, deny_gate())
    monkeypatch.setattr(refine_engine, "llm", lambda: metered)
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            response = client.post("/hospital/search", json={
                "origin": [37.5, 127.0],
                "utterance": "가까운 곳",
                "transport": "none",
            })
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "policy_denied"
    assert spy.calls == 0


def test_shipped_usage_policy_is_deny_all_and_independent_of_dev_console():
    fields = type(settings).model_fields
    assert fields["usage_policy"].default == "deny-all"
    # 두 값은 서로 독립이다. dev_console 이 닫혀 있다는 것이 usage_policy 를 대신하지 않는다.
    assert fields["dev_console"].default is False


def test_real_provider_factories_install_metered_adapters(monkeypatch):
    from app.discovery.refine.nl import llm

    monkeypatch.setattr(settings, "usage_policy", "deny-all")
    monkeypatch.setattr(settings, "walk_route_provider", "tmap")
    monkeypatch.setattr(settings, "tmap_app_key", "test-key")
    monkeypatch.setattr(settings, "static_map_provider", "naver")
    monkeypatch.setattr(settings, "naver_ncp_key_id", "test-id")
    monkeypatch.setattr(settings, "naver_ncp_key", "test-secret")
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")
    usage_gate.cache_clear()
    usage_composition.route_provider.cache_clear()
    provider_registry.static_map_provider.cache_clear()
    usage_composition.static_map_fetcher.cache_clear()
    try:
        assert isinstance(usage_composition.route_provider("walk"), MeteredRouteProvider)
        assert isinstance(usage_composition.static_map_fetcher(), MeteredStaticMapFetcher)
        assert isinstance(llm(), MeteredLLM)
    finally:
        usage_composition.route_provider.cache_clear()
        usage_composition.static_map_fetcher.cache_clear()
        provider_registry.static_map_provider.cache_clear()
        usage_gate.cache_clear()
