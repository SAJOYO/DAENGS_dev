import asyncio
from dataclasses import dataclass

import httpx

from app.journey.advice import dog_time_factor, walk_advice
from app.journey.contract import JourneyPlan
from app.journey.handoff import handoff_links
from app.journey.models import Leg, RoadMix, Transport
from app.journey.polyline import encode as encode_polyline
from app.journey.spots import spots_out
from app.providers.base import LatLng, Mode, RouteResult, RouteStatus
from app.providers.fake import FakeProvider, haversine_m
from app.providers.registry import route_provider_name
from app.usage.composition import route_provider
from app.usage.models import UsageDenied

_fake = FakeProvider()


@dataclass(frozen=True)
class RouteOutcome:
    result: RouteResult | None
    status: RouteStatus
    reason: str | None = None


async def _route(mode: Mode, origin: LatLng, dest: LatLng, measured: bool) -> RouteOutcome:
    name = route_provider_name(mode)
    if name == "none":
        return RouteOutcome(None, "unavailable", "provider_disabled")
    if not measured:
        return RouteOutcome(await _fake.route(mode, origin, dest), "estimate", "preview")
    if name == "fake":
        return RouteOutcome(
            await _fake.route(mode, origin, dest), "estimate", "provider_is_fake"
        )

    provider = route_provider(mode)
    if provider.name == "none":
        reason = "provider_unconfigured"
    elif mode not in provider.route_modes:
        reason = "capability_missing"
    else:
        reason = None
        try:
            result = await provider.route(mode, origin, dest)
        except UsageDenied:
            result = None
            reason = "usage_denied"
        except (httpx.HTTPError, ValueError, KeyError):
            result = None
            reason = "provider_error"
        if result:
            return RouteOutcome(result, "measured")
        reason = reason or "provider_no_route"
    return RouteOutcome(await _fake.route(mode, origin, dest), "estimate", reason)


def _unavailable_leg(mode: Mode, outcome: RouteOutcome) -> Leg:
    return Leg(
        status="unavailable",
        status_reason=outcome.reason,
        source=route_provider_name(mode),
    )


def _leg(
    outcome: RouteOutcome,
    factor: float,
    advice: tuple[str, list[str]] | None = None,
) -> Leg:
    result = outcome.result
    assert result is not None
    facilities = result.facilities if outcome.status == "measured" else None
    return Leg(
        status=outcome.status,
        status_reason=outcome.reason,
        min=max(1, round(result.duration_s * factor / 60)),
        provider_min=max(1, round(result.duration_s / 60)) if factor != 1.0 else None,
        m=result.distance_m,
        source=result.source,
        facilities=facilities.__dict__ if facilities else None,
        road_mix=(
            RoadMix(
                big_road_m=facilities.big_road_m,
                total_m=facilities.total_m,
                big_ratio=facilities.big_road_ratio,
                big_crossings=facilities.big_crossings,
            )
            if facilities
            else None
        ),
        taxi_fare=result.taxi_fare,
        fare=result.fare,
        advice=advice[0] if advice else None,
        why=advice[1] if advice else [],
    )


async def snapshot(
    plan: JourneyPlan,
    dest: LatLng,
    *,
    dest_name: str = "",
    with_polyline: bool = True,
    arrive_note: str | None = None,
) -> Transport:
    origin = LatLng(plan.origin_lat, plan.origin_lng)
    straight = int(haversine_m(origin, dest))
    dog = plan.companion == "dog"
    show_transit = "transit" in plan.mode_priority
    measured_mode = plan.mode_priority[0] if plan.measured and plan.mode_priority else None

    walk_task = asyncio.create_task(_route("walk", origin, dest, measured_mode == "walk"))
    car_task = asyncio.create_task(_route("car", origin, dest, measured_mode == "car"))
    transit_task = (
        asyncio.create_task(_route("transit", origin, dest, measured_mode == "transit"))
        if show_transit
        else None
    )
    walk = await walk_task
    car = await car_task
    transit = await transit_task if transit_task else None

    factor = dog_time_factor() if dog else 1.0
    if walk.result is None:
        walk_leg = _unavailable_leg("walk", walk)
    else:
        advice = walk_advice(walk.result, plan.walk.max_walk_min, factor) if dog else None
        walk_leg = _leg(walk, factor, advice)
        walk_leg.spots = spots_out(walk.result, plan.companion, arrive_note)
        walk_leg.handoff = handoff_links(origin, dest, dest_name, "walk")
        if with_polyline and walk.result.polyline and walk.status == "measured":
            walk_leg.polyline = encode_polyline(
                [(point.lat, point.lng) for point in walk.result.polyline]
            )
            walk_leg.polyline_points = len(walk.result.polyline)

    car_leg = _leg(car, 1.0) if car.result else _unavailable_leg("car", car)
    car_leg.handoff = handoff_links(origin, dest, dest_name, "car")
    transit_leg = None
    if transit:
        transit_leg = (
            _leg(transit, 1.0)
            if transit.result
            else _unavailable_leg("transit", transit)
        )
        transit_leg.handoff = handoff_links(origin, dest, dest_name, "transit")

    return Transport(
        as_of=plan.resolved_at,
        companion=plan.companion,
        straight_m=straight,
        mode_priority=list(plan.mode_priority),
        walk=walk_leg,
        car=car_leg,
        transit=transit_leg,
    )
