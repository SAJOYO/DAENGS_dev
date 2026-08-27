"""편집 상태와 서버 사실을 엔진별 실행 계획으로 조립하는 유일한 관문."""

from dataclasses import dataclass

from app.discovery.facts import RuntimeFacts
from app.discovery.plans import ViewPlan
from app.discovery.semantics import UrgencySignal, planning_urgency, safety_urgency
from app.discovery.state import EditableState
from app.discovery.trace import ResolutionTrace
from app.geo.contract import SearchMust, SearchPlan, SearchPrefer
from app.geo.ranking import preference_tags
from app.journey.contract import Companion, JourneyPlan, WalkPlan
from app.providers.base import Mode


@dataclass(frozen=True)
class ResolvedRequest:
    """각 엔진은 자기 plan만 받고, trace는 응답·감사에만 쓴다."""

    search: SearchPlan
    journey: JourneyPlan
    view: ViewPlan
    trace: ResolutionTrace


def _available_modes(state: EditableState, facts: RuntimeFacts,
                     companion: Companion) -> list[Mode]:
    modes: list[Mode] = ["walk", "car"]
    if companion == "none":
        modes.append("transit")
    else:
        dog_allows = facts.profile is None or facts.profile.size_class == "small"
        owner_allows = facts.owner is None or facts.owner.transit_ok is not False
        if dog_allows and owner_allows:
            modes.append("transit")

    preferred = state.journey.preferred_mode
    if preferred in modes:
        modes.remove(preferred)
        modes.insert(0, preferred)
    return modes


def resolve_request(
    state: EditableState,
    facts: RuntimeFacts,
    *,
    kind: str | None,
    companion: Companion,
    measured: bool,
    transport_available: bool = True,
) -> ResolvedRequest:
    """한 번 읽고 세 계획을 만든다. 엔진은 state·facts를 다시 보지 않는다."""
    if facts.now.tzinfo is None:
        raise ValueError("RuntimeFacts.now must include a timezone")

    trace = ResolutionTrace()
    signals = list(facts.urgency_signals)
    if state.urgency is not None:
        signals.append(UrgencySignal(value=state.urgency, origin="user"))
    plan_urgency = planning_urgency(signals)
    safe_urgency, safety_reasons = safety_urgency(signals)

    judge_at = facts.now
    departure_at = facts.now
    if state.time_intent is not None:
        if state.time_intent.kind == "service_at":
            judge_at = state.time_intent.at
            trace.note("target", f"진료 시각 {judge_at.isoformat()}", because="time_intent.service_at")
        elif state.time_intent.kind == "depart_at":
            departure_at = state.time_intent.at
            trace.note("journey", f"출발 시각 {departure_at.isoformat()}", because="time_intent.depart_at")
        else:
            trace.note("context", "도착 기한은 후보별 경로 계산 후 판정", because="time_intent.arrive_by")

    # 의미 규칙은 공용(geo/ranking.preference_tags) — 두 진입 경로가 같은 해석을 쓴다 (#24).
    # 상황 정책(긴급도가 응급 선호를 켠다)은 여기 남는다: 무엇을 선호로 볼지와
    # 언제 그것을 켤지는 다른 결정이다.
    prefer = set(preference_tags(
        night=state.target.night_service,
        emergency=state.target.emergency_service or plan_urgency == "urgent",
    ))

    open_now = state.target.open_now or plan_urgency == "urgent"
    search = SearchPlan(
        must=SearchMust(
            lat=state.lat,
            lng=state.lng,
            radius_m=state.target.radius_m,
            judge_at=judge_at,
            kind=kind,
            open_now=open_now,
            require_tags=tuple(state.target.require_tags),
            exclude_ids=tuple(state.target.exclude_ids),
            limit=state.target.limit,
        ),
        prefer=SearchPrefer(tags=tuple(sorted(prefer))),
    )

    modes = _available_modes(state, facts, companion)
    if state.journey.preferred_mode and state.journey.preferred_mode not in modes:
        trace.note(
            "journey",
            f"{state.journey.preferred_mode} 이용 조건 불충족",
            because="profile/owner transit constraint",
            overrode="journey.preferred_mode",
        )
    if plan_urgency == "urgent":
        if "car" in modes:
            modes.remove("car")
            modes.insert(0, "car")
        overrode = "journey.preferred_mode" if state.journey.preferred_mode not in (None, "car") else ""
        trace.note("journey", "수단 우선순위 " + ">".join(modes),
                   because="planning_urgency=urgent", overrode=overrode)
        if not state.target.open_now:
            trace.note("target", "확정 영업 종료 제외", because="planning_urgency=urgent")

    journey = JourneyPlan(
        origin_lat=state.lat,
        origin_lng=state.lng,
        resolved_at=facts.now,
        departure_at=departure_at,
        companion=companion,
        measured=measured,
        mode_priority=tuple(modes),
        max_total_min=state.journey.max_total_min,
        hard_limit=state.journey.hard_limit,
        walk=WalkPlan(max_walk_min=state.journey.walk.max_walk_min),
        profile=facts.profile,
        temp_c=facts.temp_c,
    )

    view_sort = "duration" if plan_urgency == "urgent" and transport_available else state.sort
    if view_sort != state.sort:
        trace.note("view", "소요시간순", because="planning_urgency=urgent", overrode="sort")
    elif plan_urgency == "urgent" and not transport_available:
        trace.note("view", "기존 정렬 유지", because="transport unavailable for duration sort")
    view = ViewPlan(
        sort=view_sort,
        pin_ids=tuple(state.target.pin_ids),
        show_call_cta=safe_urgency == "urgent",
        call_reasons=tuple(safety_reasons),
    )
    return ResolvedRequest(search=search, journey=journey, view=view, trace=trace)
