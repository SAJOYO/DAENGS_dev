"""Explicit offline contract exercise. Recorded public places, fixed example intents.

The production compiler, dog evaluator and response assembler are real; Gemini and DB are
replaced here only. No fixture is imported by production modules or live review routes.
"""

import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import httpx
from fastapi import HTTPException
from memory_sessions import MemorySessions
from pydantic import ValidationError

from daengs_backend.schemas.facility_discovery import FacilityActionRequest
from daengs_backend.schemas.facility_discovery import FacilityDiscoveryRequest as PublicRequest
from daengs_backend.services.facility_discovery import (
    FacilityDiscoveryError,
    FacilityDiscoveryService,
)
from daengs_place.place.discovery.facility import (
    FacilityDiscoveryRequest,
    FacilityInternalAction,
    continue_facilities,
    start_facilities,
)
from daengs_place.place.discovery.service import PlaceDiscoveryService
from daengs_place.place.intent.contract import (
    EvidenceQuote,
    IntentInterpretation,
    LLMIntentOutput,
    LLMIntentProposal,
    ProposalDisposition,
)
from daengs_place.place.intent.service import PlaceIntentSuggestionService
from daengs_place.place.planning.execution import prefers_parking, purpose_kinds
from daengs_place.place.planning.intents import (
    BooleanCapabilityIntent,
    IntentRole,
    KindIntent,
    SemanticIntent,
)
from daengs_place.place.search import (
    PlaceSearchGroup,
    PlaceSearchRequest,
    PlaceSearchResponse,
    compile_place_search_request,
    evaluate_search_dogs,
)
from daengs_place.place.source_facts.bundle import CandidateFactBundle

EXAMPLES = {
    "주차되면 좋은 카페": [
        ("required_target", "cafe", "카페"),
        ("preference", "parking", "주차되면 좋은"),
    ],
    "조용하면 좋은 카페": [
        ("required_target", "cafe", "카페"),
        ("preference", "quiet", "조용하면 좋은"),
    ],
    "주차 필수 카페": [
        ("required_target", "cafe", "카페"),
        ("required_condition", "parking", "주차 필수"),
    ],
    "카페": [("required_target", "cafe", "카페")],
    "식당": [("required_target", "restaurant", "식당")],
    "싼 카페": [("required_target", "cafe", "카페"), ("required_condition", "cheap", "싼")],
}


class FixedProposer:
    async def propose(self, utterance):
        if utterance not in EXAMPLES:
            raise HTTPException(
                422,
                "검증 데이터 모드에서는 왼쪽의 고정 예시 문장을 사용해 주세요. 자유 검색은 실제 서버 모드에서 가능해요.",
            )
        proposals = []
        for role, value, quote in EXAMPLES[utterance]:
            intent = (
                BooleanCapabilityIntent(capability_id="operations.parking", value=True)
                if value == "parking"
                else SemanticIntent(concept_id="semantic.quiet")
                if value == "quiet"
                else SemanticIntent(concept_id="semantic.cheap")
                if value == "cheap"
                else KindIntent(kind=value)
            )
            proposals.append(
                LLMIntentProposal(
                    role=IntentRole(role),
                    intent=intent,
                    evidence=EvidenceQuote(quote=quote, start=None, end=None),
                )
            )
        return LLMIntentOutput(
            disposition=ProposalDisposition.PROPOSED,
            interpretations=(IntentInterpretation(proposals=tuple(proposals)),),
            reason=None,
        )


def distance(a, b, x, y):
    dlat, dlon = radians(x - a), radians(y - b)
    h = sin(dlat / 2) ** 2 + cos(radians(a)) * cos(radians(x)) * sin(dlon / 2) ** 2
    return round(6371000 * 2 * asin(min(1, sqrt(h))))


async def searcher(db, plan):
    del db
    path = Path(__file__).parent / "places.fixture.json"
    recorded = PlaceSearchResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))
    groups = []
    for kind in purpose_kinds(plan):
        hits = []
        for source in recorded.groups:
            if source.kind != kind:
                continue
            for original in source.results:
                hit = original.model_copy(deep=True)
                hit.place.distance_m = distance(
                    plan.spatial.lat, plan.spatial.lng, hit.place.lat, hit.place.lng
                )
                if (
                    hit.place.distance_m <= plan.spatial.radius_m
                    and plan.name_query.casefold() in hit.place.name.casefold()
                ):
                    hits.append(hit)
        hits.sort(
            key=lambda h: (
                h.place.distance_m // 500 if prefers_parking(plan) else h.place.distance_m,
                0 if prefers_parking(plan) and h.place.facts.parking is True else 1,
                h.place.distance_m,
                h.place.key.source,
                h.place.key.ref,
            )
        )
        groups.append(
            PlaceSearchGroup(
                kind=kind,
                limit=plan.limit_per_kind,
                truncated=len(hits) > plan.limit_per_kind,
                results=hits[: plan.limit_per_kind],
            )
        )
    return PlaceSearchResponse(groups=groups, name_query=plan.name_query)


async def loader(db, keys):
    return [CandidateFactBundle(key=key) for key in keys]


_sessions = MemorySessions()


async def fixture_transport(request):
    service = PlaceDiscoveryService(
        PlaceIntentSuggestionService(FixedProposer()), searcher=searcher, source_fact_loader=loader
    )
    payload = json.loads(request.content)
    try:
        if request.url.path.endswith("/actions"):
            result = await continue_facilities(
                None, FacilityInternalAction.model_validate(payload), service
            )
        else:
            result = await start_facilities(
                None, FacilityDiscoveryRequest.model_validate(payload), service
            )
        return httpx.Response(200, json=result.model_dump(mode="json"))
    except ValueError:
        return httpx.Response(422, json={"detail": "선택할 수 없는 조건이에요."})


async def run_fixture(mode, body, *, owner="fixture-account"):
    try:
        if mode in {"ai", "actions"}:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(fixture_transport)
            ) as client:
                service = FacilityDiscoveryService(
                    client, store=_sessions, base_url="http://fixture", timeout=55
                )
                if mode == "ai":
                    return await service.search(PublicRequest.model_validate(body), owner)
                return await service.act(FacilityActionRequest.model_validate(body), owner)
        if mode == "normal":
            request = PlaceSearchRequest.model_validate(body)
            response = await searcher(None, compile_place_search_request(request))
            return evaluate_search_dogs(response, request.dogs)
        raise HTTPException(404)
    except ValidationError as exc:
        raise HTTPException(422, "검증 요청 형식이 올바르지 않아요.") from exc
    except FacilityDiscoveryError as exc:
        errors = {
            "facility_expired": (410, "검색이 만료됐어요. 다시 검색해 주세요."),
            "facility_conflict": (409, "검색 상태가 바뀌었어요. 다시 검색해 주세요."),
            "facility_invalid_action": (422, "현재 검색에서 선택할 수 없는 조건이에요."),
        }
        status, message = errors.get(exc.code, (503, "검증 검색을 처리하지 못했어요."))
        raise HTTPException(status, detail={"code": exc.code, "message": message}) from exc
