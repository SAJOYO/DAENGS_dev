import json
from collections.abc import Iterator

import pytest

from daengs_place.place.contracts import (
    PlaceClassification,
    PlaceFacts,
    PlaceMatch,
    PlaceRef,
    PlaceResult,
)
from daengs_place.place.discovery.contract import (
    PlaceDiscoveryRequest,
    PlaceDiscoveryResultPolicy,
)
from daengs_place.place.discovery.service import PlaceDiscoveryService
from daengs_place.place.information_needs import InformationNeedId
from daengs_place.place.intent.contract import (
    EvidenceQuote,
    IntentInterpretation,
    IntentProposerInvalidOutputError,
    LLMIntentOutput,
    LLMIntentProposal,
    LLMSearchDirective,
    ProposalDisposition,
    ProposalReason,
    SearchModeId,
)
from daengs_place.place.intent.lenses import LensMappingScope
from daengs_place.place.intent.service import PlaceIntentSuggestionService
from daengs_place.place.planning.contract import (
    PlaceKind,
    PlaceSearchConditions,
    PlaceSpatialConstraint,
)
from daengs_place.place.planning.execution import purpose_kinds
from daengs_place.place.planning.intents import (
    ActivityId,
    ActivityIntent,
    IntentRole,
    KindIntent,
    ObjectIntent,
    PlannerStatus,
    SearchObjectId,
    SemanticIntent,
)
from daengs_place.place.search import (
    PlaceSearchGroup,
    PlaceSearchHit,
    PlaceSearchResponse,
)
from daengs_place.place.source_facts.bundle import (
    SourceFactKey,
    SourceFactVariant,
    build_candidate_fact_bundle,
)
from daengs_place.place.source_facts.kto import project_kto
from daengs_place.place.source_facts.states import DetailAcquisitionState, FactState

_SPATIAL = PlaceSpatialConstraint(lat=37.5563, lng=126.9236, radius_m=3_000)


class _OutputProposer:
    def __init__(self, output: LLMIntentOutput):
        self._output = output
        self.utterances: list[str] = []

    async def propose(self, utterance: str) -> LLMIntentOutput:
        self.utterances.append(utterance)
        return self._output


class _InvalidOutputProposer:
    async def propose(self, utterance: str) -> LLMIntentOutput:
        del utterance
        raise IntentProposerInvalidOutputError(
            "provider schema failure",
            raw_output="sensitive provider output",
        )


def _proposal(role: IntentRole, intent, quote: str) -> LLMIntentProposal:
    return LLMIntentProposal(
        role=role,
        intent=intent,
        evidence=EvidenceQuote(quote=quote, start=None, end=None),
    )


def _output(
    *proposals: LLMIntentProposal,
    disposition: ProposalDisposition = ProposalDisposition.PROPOSED,
) -> LLMIntentOutput:
    return LLMIntentOutput(
        disposition=disposition,
        interpretations=(IntentInterpretation(proposals=proposals),),
        reason=(
            ProposalReason.MULTIPLE_PLAUSIBLE_READINGS
            if disposition is ProposalDisposition.AMBIGUOUS
            else None
        ),
    )


def _ids(prefix: str = "discovery") -> Iterator[str]:
    for index in range(1, 200):
        yield f"{prefix}-observation-{index}"


def _intent_service(output: LLMIntentOutput) -> PlaceIntentSuggestionService:
    ids = _ids()
    return PlaceIntentSuggestionService(
        _OutputProposer(output),
        observation_id_factory=lambda: next(ids),
    )


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


def _request(
    query: str,
    *,
    policy: PlaceDiscoveryResultPolicy | None = None,
    conditions: PlaceSearchConditions | None = None,
) -> PlaceDiscoveryRequest:
    return PlaceDiscoveryRequest(
        query=query,
        spatial=_SPATIAL,
        conditions=conditions,
        result_policy=policy or PlaceDiscoveryResultPolicy(),
    )


async def test_plan_preserves_trusted_values_and_excludes_provider_trace() -> None:
    output = _output(_proposal(IntentRole.REQUIRED_TARGET, KindIntent(kind=PlaceKind.CAFE), "카페"))
    proposer = _OutputProposer(output)
    ids = _ids("planning")
    conditions = PlaceSearchConditions(dog_size="small")
    service = PlaceDiscoveryService(
        PlaceIntentSuggestionService(
            proposer,
            observation_id_factory=lambda: next(ids),
        )
    )
    request = _request("  카페 찾아줘  ", conditions=conditions)

    planning = await service.plan(request)
    payload = planning.model_dump(mode="json")

    assert planning.status is PlannerStatus.READY
    assert proposer.utterances == [request.query]
    assert planning.contract_version == "place-discovery-planning-v1"
    target = planning.lenses.executable_targets[0]
    assert target.candidate.result.plan is not None
    assert target.candidate.result.plan.spatial == _SPATIAL
    assert target.candidate.result.plan.conditions == conditions
    assert "raw" not in payload
    assert "grounded" not in payload
    assert "normalized" not in payload


async def test_invalid_provider_output_becomes_issue_without_raw_leak() -> None:
    service = PlaceDiscoveryService(PlaceIntentSuggestionService(_InvalidOutputProposer()))

    planning = await service.plan(_request("강아지가 좋아하는 곳"))
    encoded = json.dumps(planning.model_dump(mode="json"), ensure_ascii=False)

    assert planning.status is PlannerStatus.NEEDS_CLARIFICATION
    assert planning.source_disposition is None
    assert not planning.lenses.target_lenses
    assert [issue.code for issue in planning.issues] == ["intent_proposer_invalid_output"]
    assert "provider schema failure" not in encoded
    assert "sensitive provider output" not in encoded


async def test_open_discovery_keeps_three_lenses_without_choosing_one() -> None:
    output = LLMIntentOutput(
        disposition=ProposalDisposition.PROPOSED,
        interpretations=(
            IntentInterpretation(
                search_directive=LLMSearchDirective(
                    mode=SearchModeId.OPEN_DISCOVERY,
                    evidence=EvidenceQuote(
                        quote="네가 추천해봐",
                        start=None,
                        end=None,
                    ),
                ),
                proposals=(),
            ),
        ),
        reason=None,
    )

    planning = await PlaceDiscoveryService(_intent_service(output)).plan(
        _request("오늘 심심한데 네가 추천해봐")
    )

    assert [item.display_label for item in planning.lenses.target_lenses] == [
        "#먹고 쉬기",
        "#가볍게 나가기",
        "#구경하기",
    ]
    assert all(
        item.mapping_scope is LensMappingScope.OPEN_DISCOVERY
        for item in planning.lenses.target_lenses
    )
    assert len(planning.lenses.executable_targets) == 3


async def test_lens_budget_executes_a_stable_prefix_and_discloses_it() -> None:
    output = LLMIntentOutput(
        disposition=ProposalDisposition.PROPOSED,
        interpretations=(
            IntentInterpretation(
                search_directive=LLMSearchDirective(
                    mode=SearchModeId.OPEN_DISCOVERY,
                    evidence=EvidenceQuote(
                        quote="추천해봐",
                        start=None,
                        end=None,
                    ),
                ),
                proposals=(),
            ),
        ),
        reason=None,
    )
    searched = []

    async def searcher(db, plan):
        del db
        searched.append(plan)
        return PlaceSearchResponse(
            groups=[
                PlaceSearchGroup(
                    kind=purpose_kinds(plan)[0],
                    limit=plan.limit_per_kind,
                    results=[],
                )
            ]
        )

    async def loader(db, keys):
        del db
        assert not keys
        return []

    policy = PlaceDiscoveryResultPolicy(
        max_executable_lenses=1,
        max_total_candidates=5,
    )
    result = await PlaceDiscoveryService(
        _intent_service(output),
        searcher=searcher,
        source_fact_loader=loader,
    ).discover(None, _request("오늘 심심한데 추천해봐", policy=policy))  # type: ignore[arg-type]

    assert len(searched) == 1
    assert [item.display_label for item in result.lens_results] == ["#먹고 쉬기"]
    assert "discovery.lens_budget_applied" in {item.code for item in result.notices}


async def test_discovery_assembles_source_facts_and_information_needs() -> None:
    output = _output(
        _proposal(IntentRole.REQUIRED_TARGET, KindIntent(kind=PlaceKind.TRAVEL), "여행지"),
        _proposal(
            IntentRole.PREFERENCE,
            SemanticIntent(concept_id="semantic.quiet"),
            "조용한",
        ),
    )
    searched = []

    async def searcher(db, plan):
        del db
        searched.append(plan)
        return PlaceSearchResponse(
            conditions=plan.conditions,
            groups=[
                PlaceSearchGroup(
                    kind=purpose_kinds(plan)[0],
                    limit=plan.limit_per_kind,
                    results=[PlaceSearchHit(place=_place("K1"))],
                )
            ],
        )

    loaded = []

    async def loader(db, keys):
        del db
        loaded.extend(keys)
        projection = project_kto(
            {"contenttypeid": "12"},
            {"acmpyTypeCd": "전구역 동반가능"},
            detail_state=FactState.KNOWN,
        )
        return [
            build_candidate_fact_bundle(
                key,
                [
                    SourceFactVariant(
                        source_ref=key.source_ref,
                        record_ref="record:1",
                        occurrence_count=1,
                        snapshot="test-snapshot",
                        detail_state=DetailAcquisitionState.FETCHED,
                        projection=projection,
                    )
                ],
            )
            for key in keys
        ]

    result = await PlaceDiscoveryService(
        _intent_service(output),
        searcher=searcher,
        source_fact_loader=loader,
    ).discover(None, _request("조용한 여행지 찾아줘"))  # type: ignore[arg-type]

    assert len(searched) == 1
    assert loaded == [SourceFactKey(source="kto", source_ref="K1")]
    assert result.contract_version == "place-discovery-v1"
    lens = result.lens_results[0]
    assert lens.information_needs == (InformationNeedId.AMBIENCE_QUIET,)
    assert lens.presentations[0].place_key == PlaceRef(source="kto", ref="K1")
    assert "ambience.quiet.unavailable" in {item.code for item in lens.presentations[0].notices}
    assert result.serialized_size_bytes <= result.result_policy.max_serialized_bytes


async def test_non_executable_planning_never_searches_or_loads_facts() -> None:
    output = LLMIntentOutput(
        disposition=ProposalDisposition.ABSTAINED,
        interpretations=(),
        reason=ProposalReason.INSUFFICIENT_TARGET,
    )
    calls = []

    async def searcher(db, plan):
        calls.append((db, plan))
        raise AssertionError("search must not run")

    async def loader(db, keys):
        calls.append((db, keys))
        raise AssertionError("source fact load must not run")

    result = await PlaceDiscoveryService(
        _intent_service(output),
        searcher=searcher,
        source_fact_loader=loader,
    ).discover(None, _request("모르겠어"))  # type: ignore[arg-type]

    assert not calls
    assert not result.lens_results


@pytest.mark.parametrize(
    ("query", "output", "expected_need", "expected_lenses"),
    [
        (
            "강아지랑 놀고 싶음",
            _output(
                _proposal(
                    IntentRole.GOAL,
                    ActivityIntent(activity_id=ActivityId.PLAY),
                    "놀고 싶음",
                )
            ),
            InformationNeedId.ACTIVITY_PLAY,
            3,
        ),
        (
            "강아지 장난감 사고 싶어",
            _output(
                _proposal(
                    IntentRole.GOAL,
                    ObjectIntent(object_id=SearchObjectId.DOG_TOY),
                    "강아지 장난감",
                ),
                _proposal(
                    IntentRole.GOAL,
                    ActivityIntent(activity_id=ActivityId.BUY),
                    "사고 싶어",
                ),
            ),
            InformationNeedId.PRODUCTS_PURCHASABLE,
            1,
        ),
    ],
)
async def test_composed_meaning_reaches_every_discovery_lens(
    query: str,
    output: LLMIntentOutput,
    expected_need: InformationNeedId,
    expected_lenses: int,
) -> None:
    async def searcher(db, plan):
        del db
        return PlaceSearchResponse(
            conditions=plan.conditions,
            groups=[
                PlaceSearchGroup(
                    kind=purpose_kinds(plan)[0],
                    limit=plan.limit_per_kind,
                    results=[],
                )
            ],
        )

    async def loader(db, keys):
        del db
        assert not keys
        return []

    result = await PlaceDiscoveryService(
        _intent_service(output),
        searcher=searcher,
        source_fact_loader=loader,
    ).discover(None, _request(query))  # type: ignore[arg-type]

    assert len(result.lens_results) == expected_lenses
    assert all(item.information_needs == (expected_need,) for item in result.lens_results)


async def test_candidate_budgets_are_allocated_before_fact_loading() -> None:
    output = LLMIntentOutput(
        disposition=ProposalDisposition.AMBIGUOUS,
        interpretations=tuple(
            IntentInterpretation(
                proposals=(_proposal(IntentRole.REQUIRED_TARGET, KindIntent(kind=kind), quote),)
            )
            for kind, quote in (
                (PlaceKind.CAFE, "카페"),
                (PlaceKind.PET_SHOP, "펫샵"),
                (PlaceKind.TRAVEL, "여행지"),
            )
        ),
        reason=ProposalReason.MULTIPLE_PLAUSIBLE_READINGS,
    )
    search_index = 0

    async def searcher(db, plan):
        nonlocal search_index
        del db
        search_index += 1
        kind = purpose_kinds(plan)[0]
        return PlaceSearchResponse(
            groups=[
                PlaceSearchGroup(
                    kind=kind,
                    limit=plan.limit_per_kind,
                    results=[
                        PlaceSearchHit(place=_place(f"K{search_index}-{index}", kind=kind.value))
                        for index in range(10)
                    ],
                )
            ]
        )

    loaded = []

    async def loader(db, keys):
        del db
        loaded.extend(keys)
        return [build_candidate_fact_bundle(key, []) for key in keys]

    policy = PlaceDiscoveryResultPolicy(
        max_executable_lenses=3,
        max_candidates_per_lens=4,
        max_total_candidates=7,
    )
    result = await PlaceDiscoveryService(
        _intent_service(output),
        searcher=searcher,
        source_fact_loader=loader,
    ).discover(None, _request("카페 펫샵 여행지", policy=policy))  # type: ignore[arg-type]

    assert [len(item.presentations) for item in result.lens_results] == [3, 2, 2]
    assert [item.candidate_limit for item in result.lens_results] == [3, 2, 2]
    assert len(loaded) == 7
    assert sum(len(item.presentations) for item in result.lens_results) == 7
    assert {(notice.code, notice.lens_id) for notice in result.notices} >= {
        ("discovery.candidate_budget_applied", item.lens_id) for item in result.lens_results
    }


async def test_source_fact_loader_must_preserve_requested_identity_order() -> None:
    output = _output(_proposal(IntentRole.REQUIRED_TARGET, KindIntent(kind=PlaceKind.CAFE), "카페"))

    async def searcher(db, plan):
        del db
        return PlaceSearchResponse(
            groups=[
                PlaceSearchGroup(
                    kind=PlaceKind.CAFE,
                    limit=plan.limit_per_kind,
                    results=[
                        PlaceSearchHit(place=_place("K1", kind="cafe")),
                        PlaceSearchHit(place=_place("K2", kind="cafe")),
                    ],
                )
            ]
        )

    async def loader(db, keys):
        del db
        return [build_candidate_fact_bundle(key, []) for key in reversed(keys)]

    service = PlaceDiscoveryService(
        _intent_service(output),
        searcher=searcher,
        source_fact_loader=loader,
    )
    with pytest.raises(RuntimeError, match="preserve every requested key in order"):
        await service.discover(None, _request("카페"))  # type: ignore[arg-type]


async def test_serialized_byte_budget_drops_tail_candidates_with_notice() -> None:
    output = _output(_proposal(IntentRole.REQUIRED_TARGET, KindIntent(kind=PlaceKind.CAFE), "카페"))

    async def searcher(db, plan):
        del db
        return PlaceSearchResponse(
            groups=[
                PlaceSearchGroup(
                    kind=PlaceKind.CAFE,
                    limit=plan.limit_per_kind,
                    results=[
                        PlaceSearchHit(place=_place(f"large-{index}", address="가" * 500))
                        for index in range(5)
                    ],
                )
            ]
        )

    async def loader(db, keys):
        del db
        return [build_candidate_fact_bundle(key, []) for key in keys]

    policy = PlaceDiscoveryResultPolicy(max_serialized_bytes=16 * 1024)
    result = await PlaceDiscoveryService(
        _intent_service(output),
        searcher=searcher,
        source_fact_loader=loader,
    ).discover(None, _request("카페", policy=policy))  # type: ignore[arg-type]

    assert len(result.lens_results[0].presentations) < 5
    assert result.serialized_size_bytes <= policy.max_serialized_bytes
    assert "discovery.serialized_byte_budget_applied" in {item.code for item in result.notices}
