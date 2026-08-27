"""journey 엔진 — 출발지→목적지의 이동 스냅샷. 공용 (병원행·약국행·산책 코스).

- measured=True: 제공사 실측(route_provider) + 캐시, 실패 시 휴리스틱 강등. False: 휴리스틱만(호출 0)
- companion=dog: 개 계수·advice·spots 노트·대중교통 제한
- companion=none: 제공사 원값, 도착 앵커만, advice 없음 — 지도앱 수준
"""

import asyncio
from dataclasses import dataclass

import httpx

from app.geo.polyline import encode as encode_polyline
from app.journey.advice import dog_time_factor, walk_advice
from app.journey.contract import JourneyPlan
from app.journey.handoff import handoff_links
from app.journey.models import Companion, Leg, RoadMix, Transport
from app.journey.spots import spots_out
from app.providers.base import LatLng, Mode, RouteResult, RouteStatus
from app.providers.fake import FakeProvider, haversine_m
from app.providers.registry import route_provider_name
from app.usage.composition import route_cache_stats, route_provider
from app.usage.models import UsageDenied

_fake = FakeProvider()

def cache_stats() -> dict:
    return route_cache_stats()


@dataclass(frozen=True)
class RouteOutcome:
    """경로 하나와 **그 숫자를 얼마나 믿을 수 있는지**. 강등은 조용히 일어나면 안 된다."""

    result: RouteResult | None
    status: RouteStatus
    reason: str | None = None


async def _route(mode: Mode, o: LatLng, d: LatLng, measured: bool) -> RouteOutcome:
    """네 갈래뿐이다.

        설정이 none        → unavailable. 숫자를 만들지 않는다
        추정 요청(목록)     → estimate (preview)
        설정이 fake        → estimate. 실측을 요청받아도 measured 라고 하지 않는다
        실측 요청, 성공     → measured. **진짜 제공사만 이 값을 받는다**
        실측 요청, 실패     → estimate + 강등 이유. 왜 추정인지가 응답에 남는다
    """
    name = route_provider_name(mode)
    if name == "none":
        return RouteOutcome(None, "unavailable", "provider_disabled")

    if not measured:
        return RouteOutcome(await _fake.route(mode, o, d), "estimate", "preview")
    if name == "fake":
        # 개발용 대역. **실측을 요청받아도 measured 라고 하지 않는다** — 계산식을 제공사
        # 결과로 부르는 게 이 계약이 없애려는 거짓말이다. measured 는 진짜 제공사만 준다.
        return RouteOutcome(await _fake.route(mode, o, d), "estimate", "provider_is_fake")

    provider = route_provider(mode)
    if provider.name == "none":
        reason = "provider_unconfigured"          # 키 없음 — 시작 검증이 잡았어야 한다
    elif mode not in provider.route_modes:
        reason = "capability_missing"             # 그 모드를 구현 안 함 — 위와 같음
    else:
        reason = None
        try:
            r = await provider.route(mode, o, d)
        except UsageDenied:
            r = None
            reason = "usage_denied"
        except (httpx.HTTPError, ValueError, KeyError):
            r = None
            reason = "provider_error"
        if r:
            return RouteOutcome(r, "measured")
        reason = reason or "provider_no_route"
    # 강등해도 거리·시간은 준다 — 그건 모델이고, 라벨이 붙어 있으면 쓸모가 있다.
    # 시설은 안 준다 (FakeProvider 참조). 없는 계단을 경고하느니 아무 말도 안 하는 게 낫다.
    return RouteOutcome(await _fake.route(mode, o, d), "estimate", reason)


def _unavailable_leg(mode: Mode, outcome: RouteOutcome) -> Leg:
    """숫자를 만들지 않는 leg. 0 이 아니라 **없음**이다 — 클라이언트는 '경로 정보 없음'을 그려야 한다."""
    return Leg(status="unavailable", status_reason=outcome.reason, source=route_provider_name(mode))


def _leg(outcome: RouteOutcome, factor: float,
         advice: tuple[str, list[str]] | None = None) -> Leg:
    r = outcome.result
    assert r is not None
    # **실측이 아니면 시설은 없다.** fake 가 안 만들지만 여기서도 막는다 —
    # 시설은 advice(계단 경고·횡단 주의)를 먹이고, 그건 노령견 보호자가 읽는 문장이다.
    fac = r.facilities if outcome.status == "measured" else None
    return Leg(
        status=outcome.status, status_reason=outcome.reason,
        min=max(1, round(r.duration_s * factor / 60)),
        provider_min=(max(1, round(r.duration_s / 60)) if factor != 1.0 else None),
        m=r.distance_m, source=r.source,
        facilities=(fac.__dict__ if fac else None),
        road_mix=RoadMix(big_road_m=fac.big_road_m, total_m=fac.total_m, big_ratio=fac.big_road_ratio,
                         big_crossings=fac.big_crossings) if fac else None,
        taxi_fare=r.taxi_fare, fare=r.fare,
        advice=advice[0] if advice else None, why=advice[1] if advice else [],
    )


async def snapshot(plan: JourneyPlan, dest: LatLng, *, dest_name: str = "",
                   with_polyline: bool = True,
                   arrive_note: str | None = None) -> Transport:
    origin = LatLng(plan.origin_lat, plan.origin_lng)
    companion: Companion = plan.companion  # type: ignore[assignment]
    profile = plan.profile
    straight = int(haversine_m(origin, dest))
    dog = companion == "dog"
    show_transit = "transit" in plan.mode_priority
    measured_mode = plan.mode_priority[0] if plan.measured and plan.mode_priority else None

    walk_measured = measured_mode == "walk"
    # 목적지당 도보 경로는 **하나만** 받는다. 옵션 여럿을 받아 점수로 고르던 것은 결정 #66 으로
    # 없앴다 — 288경로 조사에서 추천 하나가 비교 결과와 99% 같은 선택이었고, 비교의 축이던
    # 계단은 0/288 이었다. 콜은 1/3 이 되고 선택은 사실상 그대로다.
    walk_task = asyncio.create_task(_route("walk", origin, dest, walk_measured))
    car_task = asyncio.create_task(_route("car", origin, dest, measured_mode == "car"))
    transit_task = (asyncio.create_task(_route("transit", origin, dest,
                                               measured_mode == "transit"))
                    if show_transit else None)
    walk = await walk_task
    car = await car_task
    transit = await transit_task if transit_task else None

    factor = dog_time_factor(profile) if dog else 1.0
    # 후보가 하나뿐이라 고를 것이 없다. `walk_tasks`/`walks`/`best` 라는 복수형은 옵션
    # fan-out 시절의 흔적이었고 #66 으로 근거가 사라졌다 — 이름이 없는 선택을 말하고 있었다.
    walk_r = walk.result
    if walk_r is None:
        wl = _unavailable_leg("walk", walk)
    else:
        advice = walk_advice(
            walk_r, profile, plan.walk.max_walk_min, plan.temp_c, factor,
        ) if dog else None

        wl = _leg(walk, factor, advice)
        wl.spots = spots_out(walk_r, profile, companion, arrive_note)
        wl.handoff = handoff_links(origin, dest, dest_name, "walk")
        if with_polyline and walk_r.polyline and walk.status == "measured":
            wl.polyline = encode_polyline([(p.lat, p.lng) for p in walk_r.polyline])
            wl.polyline_points = len(walk_r.polyline)

    cl = _leg(car, 1.0) if car.result else _unavailable_leg("car", car)
    cl.handoff = handoff_links(origin, dest, dest_name, "car")
    tl = None
    if transit:
        tl = _leg(transit, 1.0) if transit.result else _unavailable_leg("transit", transit)
        tl.handoff = handoff_links(origin, dest, dest_name, "transit")

    return Transport(
        as_of=plan.resolved_at,
        companion=companion,
        straight_m=straight,
        mode_priority=list(plan.mode_priority),
        walk=wl,
        car=cl,
        transit=tl,
    )
