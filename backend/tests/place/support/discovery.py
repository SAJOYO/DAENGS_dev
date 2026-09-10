"""Shared discovery builders; each test supplies its own scenario."""

from uuid import uuid4

from daengs_place.place.contracts import (
    PlaceClassification,
    PlaceFacts,
    PlaceMatch,
    PlaceRef,
    PlaceResult,
)
from daengs_place.place.discovery.facility import (
    FacilityDiscoveryRequest,
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
from daengs_place.place.planning.contract import (
    PlaceSpatialConstraint,
)
from daengs_place.place.planning.execution import purpose_kinds
from daengs_place.place.planning.intents import (
    BooleanCapabilityIntent,
    IntentRole,
    KindIntent,
    SemanticIntent,
)
from daengs_place.place.search import (
    PlaceSearchGroup,
    PlaceSearchHit,
    PlaceSearchResponse,
)
from daengs_place.place.source_facts.bundle import CandidateFactBundle


def _place(ref: str, *, kind: str = "travel", address: str = "서울 마포구") -> PlaceResult:
    key = PlaceRef(source="kto", ref=ref)
    return PlaceResult(
        key=key,
        name=f"테스트 장소 {ref}",
        lat=37.556,
        lng=126.923,
        distance_m=420,
        match=PlaceMatch(source=key, kind=kind),
        classifications=[
            PlaceClassification(
                source=key,
                source_category="12",
                kind=kind,
                mapping_version="test-v1",
            )
        ],
        facts=PlaceFacts(address=address),
    )


def request(**changes):
    values = {
        "client_request_id": uuid4(),
        "query": "카페",
        "spatial": PlaceSpatialConstraint(
            lat=37.5,
            lng=127,
            radius_m=10000,
        ),
    }
    return FacilityDiscoveryRequest(**(values | changes))


def make_service(*, required_parking=False, preferred_parking=False, cheap=False, huge=False):
    calls = []

    class Proposer:
        async def propose(self, utterance):
            calls.append(("llm", utterance))
            proposals = [
                LLMIntentProposal(
                    role=IntentRole.REQUIRED_TARGET,
                    intent=KindIntent(kind="cafe"),
                    evidence=EvidenceQuote(quote="카페", start=None, end=None),
                )
            ]
            if required_parking or preferred_parking:
                proposals.append(
                    LLMIntentProposal(
                        role=IntentRole.REQUIRED_CONDITION
                        if required_parking
                        else IntentRole.PREFERENCE,
                        intent=BooleanCapabilityIntent(
                            capability_id="operations.parking", value=True
                        ),
                        evidence=EvidenceQuote(
                            quote="주차 필수" if required_parking else "주차되면 좋은",
                            start=None,
                            end=None,
                        ),
                    )
                )
            if cheap:
                proposals.append(
                    LLMIntentProposal(
                        role=IntentRole.REQUIRED_CONDITION,
                        intent=SemanticIntent(concept_id="semantic.cheap"),
                        evidence=EvidenceQuote(quote="싼", start=None, end=None),
                    )
                )
            return LLMIntentOutput(
                disposition=ProposalDisposition.PROPOSED,
                interpretations=(IntentInterpretation(proposals=tuple(proposals)),),
                reason=None,
            )

    async def searcher(db, plan):
        calls.append(("db", plan))
        place = _place("one", kind="cafe")
        if huge:
            place.facts.address = "긴 주소 " * 80
        return PlaceSearchResponse(
            groups=[
                PlaceSearchGroup(
                    kind=kind,
                    limit=5,
                    results=[
                        PlaceSearchHit(place=place),
                    ],
                )
                for kind in purpose_kinds(plan)
            ]
        )

    async def loader(db, keys):
        return [CandidateFactBundle(key=key) for key in keys]

    return PlaceDiscoveryService(
        PlaceIntentSuggestionService(Proposer()), searcher=searcher, source_fact_loader=loader
    ), calls
