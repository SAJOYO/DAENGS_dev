"""POST /hospital/search — 검색 상태 편집기 + 검색. **가볍다.**

진입: 메뉴(state 없음→초안) · 딥링크(클라가 파라미터→state) · 재조정(edits=UI, utterance=자연어/음성).
LLM은 utterance 있을 때만. 팀 챗봇은 여기 안 옴 — 카드 딥링크로 들어올 뿐.

transport는 휴리스틱(호출 0)만 실어 리스트 비교용. 실측 경로·spots·advice는 카드를 눌렀을 때 POST /journey.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import SystemClock
from app.core.config import settings
from app.core.db import get_session
from app.discovery.facts import RuntimeFacts
from app.discovery.plans import ViewPlan
from app.discovery.refine.actions import Edit, SuggestedAction
from app.discovery.refine.engine import refine
from app.discovery.refine.nl import ToolCall
from app.discovery.refine.tools import ToolInputError
from app.discovery.resolver import resolve_request
from app.discovery.state import EditableState
from app.features.hospital.actions import build_actions
from app.geo.ranking import DISTANCE_BAND_M, DURATION_BAND_MIN, band_of
from app.geo.schemas import MapOut, PlaceOut
from app.geo.search import build_map, find_places
from app.journey.contract import Companion, JourneyPlan
from app.journey.engine import snapshot
from app.journey.models import Transport
from app.profile.source import owner_of, profile_source
from app.providers.base import LatLng
from app.usage.http import usage_http_exception
from app.usage.models import UsageDenied

router = APIRouter(prefix="/hospital", tags=["hospital"])

# 병원 도메인이 소유하는 문구. 공용 journey/spots 는 "진료"를 몰라야 한다.
ARRIVE_NOTE = "도착 — 간판·층수 확인, 진료 전 전화 권장"
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
Origin = tuple[Latitude, Longitude]
PositiveId = Annotated[int, Field(ge=1)]


class HospitalSearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dog_id: str | None = Field(None, max_length=128)
    origin: Origin | None = None                    # (lat, lng). state 없을 때 필수
    state: EditableState | None = None
    edits: list[Edit] = Field(default_factory=list, max_length=20)
    utterance: str | None = Field(None, max_length=2000)
    shown_ids: list[PositiveId] = Field(default_factory=list, max_length=100)
    transport: Literal["none", "estimate"] = "estimate"   # 검색 응답은 휴리스틱까지만. 실측은 /journey
    companion: Companion = "dog"              # 병원은 개 동반이 기본. "나만 감"이면 none

    @model_validator(mode="after")
    def location_is_required(self) -> "HospitalSearchIn":
        if self.state is None and self.origin is None:
            raise ValueError("origin or state required")
        return self


class ResultOut(PlaceOut):
    transport: Transport | None = None
    boost: int = 0                     # prefer_hit×2. 정렬 부스트 근거 (표시용)


class ResolutionOut(BaseModel):
    axis: str
    what: str
    because: str = ""
    overrode: str = ""


class HospitalSearchOut(BaseModel):
    state: EditableState
    results: list[ResultOut]
    map: MapOut
    changes: list[str]
    changes_by_policy: dict[str, list[str]] = Field(default_factory=dict)
    applied: list[Edit]
    question: str | None = None
    reply: str
    resolution: list[ResolutionOut] = Field(default_factory=list)
    show_call_cta: bool = False
    call_reasons: list[str] = Field(default_factory=list)
    actions: list[SuggestedAction] = Field(default_factory=list)


def _reply(changes: list[str], n: int, question: str | None,
           unknown_hours: int = 0, has_actions: bool = False) -> str:
    if question:
        return question
    head = " · ".join(changes)
    if not n:
        tail = " 아래 제안으로 다시 찾아볼 수 있어요." if has_actions else ""
        return f"{head}. 조건에 맞는 곳이 없어요.{tail}"
    # '지금 영업중'을 걸었는데 영업시간을 모르는 곳이 섞여 있으면 말해줘야 한다.
    # 안 그러면 전부 확정 영업중으로 읽힌다 (공공데이터엔 영업시간이 없다).
    tail = f" 그중 {unknown_hours}곳은 영업시간 미상이에요 — 전화로 확인해주세요." if unknown_hours else ""
    return f"{head}. {n}곳.{tail}"


@router.post("/search", response_model=HospitalSearchOut)
async def hospital_search(
    body: HospitalSearchIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalSearchOut:
    profile = await profile_source().get(body.dog_id) if body.dog_id else None
    owner = await owner_of(body.dog_id) if body.dog_id else None
    lat, lng = body.origin or (body.state.lat, body.state.lng)  # type: ignore[union-attr]

    try:
        r = await refine(
            body.state, [ToolCall(e.tool, e.args) for e in body.edits], body.utterance,
            body.shown_ids, profile, lat, lng, settings.default_radius_m,
        )
    except ToolInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UsageDenied as exc:
        raise usage_http_exception(exc) from exc
    st = r.state
    resolved = resolve_request(
        st,
        RuntimeFacts(now=SystemClock().now(), profile=profile, owner=owner),
        kind="hospital",
        companion=body.companion,
        measured=False,
        transport_available=body.transport == "estimate",
    )

    # 각 엔진에는 자기 plan만 준다. state·facts를 다시 주워 읽는 우회로는 없다.
    places = await find_places(db, resolved.search)

    results: list[ResultOut] = []
    for p in places:
        t = None
        if body.transport == "estimate":
            t = await snapshot(
                resolved.journey, LatLng(p.lat, p.lng),
                dest_name=p.name, with_polyline=False,
                arrive_note=ARRIVE_NOTE,
            )
        # 부스트는 find_places 가 이미 계산했다 (geo/ranking). 여기서 더하지 않는다 —
        # 같은 선호 규칙이 두 번 계산되던 것을 없앤 자리다 (#24).
        results.append(ResultOut(**p.model_dump(), transport=t))

    # journey.hard_limit: 정책 경계를 넘는 유일한 스위치. 사용자가 명시적으로 켤 때만
    dropped = 0
    if resolved.journey.hard_limit and resolved.journey.max_total_min is not None:
        keep, mode = [], resolved.journey.mode_priority[0]
        for res in results:
            leg = getattr(res.transport, mode, None) if res.transport else None
            # 시간을 모르는 곳(unavailable)은 **빼지 않는다.** 모름은 초과가 아니다
            if leg and leg.min is not None and leg.min > resolved.journey.max_total_min:
                dropped += 1
                continue
            keep.append(res)
        results = keep

    results = _sort(results, resolved.view, resolved.journey)
    must = resolved.search.must
    mp = build_map(must.lat, must.lng, must.radius_m, "hospital", must.open_now,
                   st.target.night_service, results,
                   emergency=st.target.emergency_service)
    actions = build_actions(
        st, result_count=len(results), question=r.question,
        dropped_by_hard_limit=dropped,
    )
    return HospitalSearchOut(
        state=st, results=results, map=mp, changes=r.changes, changes_by_policy=r.grouped,
        applied=[Edit(tool=c.tool, args=c.args) for c in r.applied],
        question=r.question,
        reply=_reply(r.changes, len(results), r.question,
                     sum(1 for x in results if x.open_now is None) if must.open_now else 0,
                     has_actions=bool(actions))
        + (f" ({dropped}곳은 시간 초과로 제외)" if dropped else ""),
        resolution=[ResolutionOut(**vars(entry)) for entry in resolved.trace.entries],
        show_call_cta=resolved.view.show_call_cta,
        call_reasons=list(resolved.view.call_reasons),
        actions=actions,
    )


def _sort(results: list[ResultOut], view: ViewPlan, journey: JourneyPlan) -> list[ResultOut]:
    """부스트는 '살짝 위' — 같은 밴드(500m / 5분) 안에서만 순서를 바꾼다. 거리·시간을 뒤집진 않는다."""
    def key(r: ResultOut):
        pinned = 0 if r.id in view.pin_ids else 1
        if view.sort == "open_first":
            primary, band = (0 if r.open_now else 1), (0 if r.open_now else 1)
        elif view.sort == "duration" and r.transport and journey.mode_priority:
            leg = getattr(r.transport, journey.mode_priority[0])
            primary = leg.min if (leg and leg.min is not None) else 10**6
            band = band_of(primary, DURATION_BAND_MIN)
        else:
            primary = r.distance_m
            band = band_of(primary, DISTANCE_BAND_M)
        return (pinned, band, -r.boost, primary)
    return sorted(results, key=key)
