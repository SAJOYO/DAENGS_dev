"""Place became a semantic routing destination (D-051) — what the deterministic layer must do.

Card: PR #204 (`feat/assistant-place-routing`). The reported defect was
"오늘 산책하기 좋은 곳이 어디야?" answering with walking *conditions*: Place was a
fully built capability (payload type, adapter, bounded projection — PR #196) that the
router could not select, so the only half of the intent the schema could express won.

This file pins the DETERMINISTIC half of that change, exactly as
`test_orchestration_care_boundary.py` pins the care boundary: every case supplies the
semantically correct decision and asserts what the real planner, engine and aggregator
then do with it. The live classifier is certified separately by a frozen gold set and
one 80-case regression (`gold_place_v1.jsonl`, `runner_v8.py`) — no test here spends a
provider call.

Two invariants get the most attention because they are the ones that would fail
silently:

1. **Payloads are per-capability.** The pre-v7 assembler ended in
   `else: {lat, lon}`, so Place would have inherited WalkPayload's shape. That is a
   ValidationError surfacing as top-level FAILED on precisely the queries Place was
   added to answer, which is why `test_unknown_execute_name_refuses...` asserts a
   raise rather than a fallback.
2. **One coordinate gate for the whole selection.** Place and Walk both need trusted
   coordinates, and CLARIFY is exclusive (O-8) — a mixed turn with no location must
   run neither capability, not the one that happens to be first.
"""

from __future__ import annotations

import json

import pytest

from daengs_backend.orchestration.adapters._place_contract import _DiscoveryResponse
from daengs_backend.orchestration.adapters._place_projection import (
    project_place_capability_data,
)
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    OutcomeDetail,
    PlacePayload,
    PrincipalContext,
    RouterKind,
    WalkPayload,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import assemble_route_plan, resolve_deterministic_route
from daengs_backend.orchestration.redirects import NO_CAPABILITY_MESSAGE
from daengs_backend.orchestration.semantic import (
    GeminiSemanticRouter,
    SemanticRoutingDecision,
    build_semantic_router_prompt,
)
from daengs_backend.orchestration.service import AssistantOrchestrationService

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")
LOCATION = {"location": {"lat": 37.5665, "lon": 126.978}}
UNSUPPORTED_MESSAGE = NO_CAPABILITY_MESSAGE

# The approved product routing table (PR #204), plus the negatives that keep the new
# destination from absorbing its neighbours. `decision` is what the classifier SHOULD
# return; these tests own what happens next.
PLACE_ROUTING_CASES: list[dict] = [
    # ---------------------------------------------------------------- Walk stays Walk
    {
        "id": "walk_now_suitability",
        "query": "지금 산책하기 좋아?",
        "decision": {"execute": ["walk"], "handoffs": []},
        "expect": "walk",
    },
    # ------------------------------------------------------------------- Place alone
    {
        "id": "place_walkable_destination",
        "query": "산책하기 좋은 곳 추천해줘",
        "decision": {"execute": ["place"], "handoffs": []},
        "expect": "place",
    },
    {
        "id": "place_animal_hospital",
        "query": "근처 동물병원 찾아줘",
        "decision": {"execute": ["place"], "handoffs": []},
        "expect": "place",
    },
    # -------------------------------------------------------------- Place plus Walk
    {
        "id": "place_and_walk_today",
        "query": "오늘 산책하기 좋은 곳 추천해줘",
        "decision": {"execute": ["place", "walk"], "handoffs": []},
        "expect": "place+walk",
    },
    # ----------------------------------------------------------- neighbours unchanged
    {
        "id": "training_leash_pulling",
        "query": "산책할 때 줄을 너무 당겨",
        "decision": {"execute": ["training"], "handoffs": []},
        "expect": "training",
    },
    {
        "id": "training_dislikes_bathing",
        "query": "목욕을 싫어해요",
        "decision": {"execute": ["training"], "handoffs": []},
        "expect": "training",
    },
    {
        "id": "care_bathing_frequency",
        "query": "목욕은 몇 주마다 해야 해?",
        "decision": {"execute": [], "handoffs": []},
        "expect": "unsupported",
    },
    {
        "id": "gait_handoff_limping",
        "query": "다리를 절뚝거리며 걸어요. 영상으로 봐줘",
        "decision": {"execute": [], "handoffs": ["gait"]},
        "expect": "gait",
    },
    {
        "id": "social_greeting",
        "query": "안녕하세요",
        "decision": {"execute": [], "handoffs": [], "social_intent": "greeting"},
        "expect": "social",
    },
    # ------------------------------------------ named region — Option B (D-051)
    # The region is not a coordinate and is not resolved. Place still runs, around the
    # device, and says so. Deferred: named-region geocoding and a named-region CLARIFY.
    {
        "id": "named_region_place",
        "query": "성수동에서 산책하기 좋은 곳 찾아줘",
        "decision": {"execute": ["place"], "handoffs": []},
        "expect": "place",
    },
    {
        "id": "named_region_hospital",
        "query": "부산 해운대 근처 동물병원 찾아줘",
        "decision": {"execute": ["place"], "handoffs": []},
        "expect": "place",
    },
]
_BY_ID = {case["id"]: case for case in PLACE_ROUTING_CASES}

_EXPECTED_CAPABILITIES = {
    "walk": [CapabilityName.WALK],
    "place": [CapabilityName.PLACE],
    "place+walk": [CapabilityName.PLACE, CapabilityName.WALK],
    "training": [CapabilityName.TRAINING],
    "unsupported": [],
    "gait": [],
    "social": [],
}


class ScriptedTransport:
    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> object:
        self.prompts.append(prompt)
        return self.outputs.pop(0)


class FakeAdapter:
    def __init__(self, capability: CapabilityName) -> None:
        self.capability = capability
        self.calls: list[CapabilityRequest] = []

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        self.calls.append(request)
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": f"{self.capability.value} answer"},
            elapsed_ms=1,
        )


def build_service(*outputs: object):
    transport = ScriptedTransport(*outputs)
    adapters = {name: FakeAdapter(name) for name in CapabilityName}
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine(adapters),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    return service, transport, adapters


def _run_case(case_id: str):
    case = _BY_ID[case_id]
    service, transport, adapters = build_service(json.dumps(case["decision"]))
    return service, transport, adapters, case


def _called(adapters: dict[CapabilityName, FakeAdapter]) -> list[CapabilityName]:
    return [name for name, adapter in adapters.items() if adapter.calls]


# --------------------------------------------------------- the approved routing table


@pytest.mark.parametrize("case_id", [case["id"] for case in PLACE_ROUTING_CASES])
async def test_approved_routing_case_executes_exactly_its_capabilities(case_id: str) -> None:
    """Every row of the product table, end to end through the real deterministic layer."""
    service, _, adapters, case = _run_case(case_id)
    response = await service.run(query=case["query"], principal=PRINCIPAL, context=dict(LOCATION))

    expected = _EXPECTED_CAPABILITIES[case["expect"]]
    assert sorted(_called(adapters), key=str) == sorted(expected, key=str)

    if case["expect"] == "unsupported":
        assert response.status == AssistantStatus.FAILED
        assert response.message == UNSUPPORTED_MESSAGE
    elif case["expect"] == "gait":
        assert response.status == AssistantStatus.HANDOFF
        assert [(h.target, h.reason) for h in response.handoffs] == [
            ("gait", "video_upload_required")
        ]
    elif case["expect"] == "social":
        assert response.status == AssistantStatus.ANSWERED
        assert response.results == [] and response.handoffs == []
    else:
        assert response.status == AssistantStatus.ANSWERED
    # Nothing in this table asks a question the router cannot answer, so no case may
    # produce a CLARIFY when trusted coordinates are present.
    assert response.clarify is None


@pytest.mark.parametrize(
    "case_id", [c["id"] for c in PLACE_ROUTING_CASES if c["expect"] in {"place", "place+walk"}]
)
async def test_place_payload_carries_the_original_query_and_trusted_coordinates(
    case_id: str,
) -> None:
    service, _, adapters, case = _run_case(case_id)
    await service.run(query=case["query"], principal=PRINCIPAL, context=dict(LOCATION))
    [request] = adapters[CapabilityName.PLACE].calls
    assert request.payload == PlacePayload(
        query=case["query"], lat=37.5665, lon=126.978
    )


async def test_place_query_text_is_preserved_byte_for_byte() -> None:
    """`PlacePayload` does not strip: the Place service grounds its interpretation in
    literal spans of the original query, so trimming here would move those offsets."""
    padded = "  근처 조용한 카페  "
    service, _, adapters = build_service(json.dumps({"execute": ["place"], "handoffs": []}))
    await service.run(query=padded, principal=PRINCIPAL, context=dict(LOCATION))
    [request] = adapters[CapabilityName.PLACE].calls
    assert request.payload.query == padded


# ------------------------------------------------------------ payload dispatch (F1)


def test_place_and_walk_get_their_own_payload_types_in_one_plan() -> None:
    """The mixed plan is where the old `else` fallback would have failed."""
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["place", "walk"], handoffs=[]),
        query="오늘 산책하기 좋은 곳 추천해줘",
        context=dict(LOCATION),
        router=RouterKind.LLM,
    )
    by_capability = {request.capability: request.payload for request in plan.requests}
    assert by_capability[CapabilityName.PLACE] == PlacePayload(
        query="오늘 산책하기 좋은 곳 추천해줘", lat=37.5665, lon=126.978
    )
    assert by_capability[CapabilityName.WALK] == WalkPayload(lat=37.5665, lon=126.978)
    # Walk never gains a `query`, and Place never loses one.
    assert not hasattr(by_capability[CapabilityName.WALK], "query")


@pytest.mark.parametrize("order", [["place", "walk"], ["walk", "place"]])
def test_execution_order_is_canonical_whatever_order_the_router_listed(
    order: list[str],
) -> None:
    """The user-visible section order must not depend on the model's list order.

    A live v7 probe returned the pair both ways round for the same shape of question,
    and `aggregate_results` builds "[산책] … [장소] …" straight from this list — so
    without a canonical order the same question can come back arranged two ways.
    Walk precedes Place, following the `CapabilityName` declaration order.
    """
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=order, handoffs=[]),
        query="오늘 산책하기 좋은 곳 추천해줘",
        context=dict(LOCATION),
        router=RouterKind.LLM,
    )
    assert [request.capability for request in plan.requests] == [
        CapabilityName.WALK,
        CapabilityName.PLACE,
    ]


def test_canonical_order_covers_every_execute_name() -> None:
    """Four destinations, one fixed order — and it matches the contract enum."""
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["place", "walk", "life", "training"], handoffs=[]),
        query="다 알려줘",
        context=dict(LOCATION),
        router=RouterKind.LLM,
    )
    assert [request.capability.value for request in plan.requests] == [
        "training",
        "life",
        "walk",
        "place",
    ]
    # `general` (D-057) sits last: additive to the four, so its section always renders last.
    assert [name.value for name in CapabilityName] == [
        "training",
        "life",
        "walk",
        "place",
        "general",
        # `vet_contact` 는 맨 끝이고 라우터의 목적지가 아니다 — 결정적 어휘 게이트와
        # 명시적 신호로만 닿는다.
        "vet_contact",
        # `care_log` (D-075) 도 라우터 목적지가 아니다. 순서에서 뒤인 것은 실행 순서가
        # 아니라 선언 순서다 — 이 능력은 배타로 단독 실행되므로 같이 정렬될 일이 없다.
        "care_log",
        # `skin` (D-079) — 배타 단일 요청이라 순서는 화면에 안 드러난다. 선언 순서 그대로 맨 끝.
        "skin",
    ]


def test_unknown_execute_name_refuses_to_inherit_another_payload() -> None:
    """A future destination must state its payload or stop the request loudly.

    Constructed by bypassing the schema on purpose — that is the only way to reach the
    branch, and reaching it is the point: the pre-v7 code answered this case by
    silently building a WalkPayload.
    """
    decision = SemanticRoutingDecision.model_construct(execute=["journey"], handoffs=[])
    with pytest.raises(ValueError, match="no payload rule for capability"):
        assemble_route_plan(
            decision, query="아무거나", context=dict(LOCATION), router=RouterKind.LLM
        )


# ---------------------------------------------------------- the coordinate gate (O-8)


@pytest.mark.parametrize(
    ("execute", "expected_question"),
    [
        (["place"], "장소를 찾을 위치의 위도와 경도를 알려주세요."),
        (["walk"], "산책할 위치의 위도와 경도를 알려주세요."),
        (
            ["place", "walk"],
            "지금 위치의 위도와 경도를 알려주시면 산책 조건과 주변 장소를 함께 찾아볼게요.",
        ),
    ],
)
async def test_missing_coordinates_clarify_once_and_execute_nothing(
    execute: list[str], expected_question: str
) -> None:
    service, _, adapters = build_service(json.dumps({"execute": execute, "handoffs": []}))
    response = await service.run(query="장소 찾아줘", principal=PRINCIPAL)  # no context
    assert response.status == AssistantStatus.CLARIFY
    assert response.clarify is not None
    assert response.clarify.missing == ["location.lat", "location.lon"]
    assert response.clarify.question == expected_question
    # Exclusive: nothing ran, and the Walk half of a mixed turn did not sneak through.
    assert response.results == [] and response.handoffs == []
    assert _called(adapters) == []


async def test_mixed_place_walk_and_handoff_without_coordinates_suppresses_all_three() -> None:
    service, _, adapters = build_service(
        json.dumps({"execute": ["place", "walk"], "handoffs": ["gait"]})
    )
    response = await service.run(query="장소랑 산책이랑 보행도 봐줘", principal=PRINCIPAL)
    assert response.status == AssistantStatus.CLARIFY
    assert response.results == [] and response.handoffs == []
    assert _called(adapters) == []


def test_out_of_range_coordinates_are_missing_not_clamped() -> None:
    """A coordinate we do not trust is not a coordinate. Place must not be answered
    confidently about a location the contract rejected."""
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["place"], handoffs=[]),
        query="근처 카페",
        context={"location": {"lat": 51.5, "lon": 126.978}},  # London latitude
        router=RouterKind.LLM,
    )
    assert plan.requests == []
    assert plan.clarify is not None and plan.clarify.missing == ["location.lat"]


def test_partial_coordinates_name_only_the_missing_axis() -> None:
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["place", "walk"], handoffs=[]),
        query="근처 카페",
        context={"location": {"lat": 37.5665}},
        router=RouterKind.LLM,
    )
    assert plan.clarify is not None and plan.clarify.missing == ["location.lon"]
    assert plan.clarify.question == "지금 위치의 경도를 알려주세요."


def test_training_and_life_still_run_without_any_location() -> None:
    """Widening the gate to Place must not start gating the question capabilities."""
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["training", "life"], handoffs=[]),
        query="입질 교육이랑 등록 절차 알려줘",
        context={},
        router=RouterKind.LLM,
    )
    assert plan.clarify is None
    assert [request.capability for request in plan.requests] == [
        CapabilityName.TRAINING,
        CapabilityName.LIFE,
    ]


# ------------------------------------------------- deterministic signal is unchanged


async def test_requested_place_still_bypasses_gemini_with_the_same_payload() -> None:
    """`requested_capability=place` keeps PR #196's behaviour after the refactor that
    deleted its separate branch — same payload, still no provider call."""
    service, transport, adapters = build_service()
    response = await service.run(
        query="  조용한 곳  ",
        principal=PRINCIPAL,
        requested_capability="place",
        context=dict(LOCATION),
    )
    assert response.status == AssistantStatus.ANSWERED
    assert transport.prompts == []
    [request] = adapters[CapabilityName.PLACE].calls
    assert request.payload == PlacePayload(query="  조용한 곳  ", lat=37.5665, lon=126.978)


async def test_requested_place_without_coordinates_still_clarifies_without_gemini() -> None:
    service, transport, adapters = build_service()
    response = await service.run(
        query="조용한 곳", principal=PRINCIPAL, requested_capability="place"
    )
    assert response.status == AssistantStatus.CLARIFY
    assert response.clarify is not None
    assert response.clarify.missing == ["location.lat", "location.lon"]
    assert response.clarify.question == "장소를 찾을 위치의 위도와 경도를 알려주세요."
    assert transport.prompts == []
    assert _called(adapters) == []


def test_deterministic_and_semantic_paths_build_the_identical_place_plan() -> None:
    """The refactor's whole justification: one Place plan, not two that can drift."""
    query = "근처 동물병원 찾아줘"
    deterministic = resolve_deterministic_route(
        requested_capability="place", query=query, context=dict(LOCATION)
    )
    semantic = assemble_route_plan(
        SemanticRoutingDecision(execute=["place"], handoffs=[]),
        query=query,
        context=dict(LOCATION),
        # 관측 메타데이터는 결정적 경로의 값으로 맞춰 둔다 — 여기서 보는 것은 "누가 골랐나" 가
        # 아니라 조립된 plan 이 같은가다.
        router=RouterKind.DETERMINISTIC,
        model=None,
        prompt_version=None,
    )
    assert deterministic == semantic


def _discovery(*, candidates: int, extra_notices: int = 0) -> _DiscoveryResponse:
    """A minimal internal discovery envelope, validated by the real consumer contract.

    `presentations` and search hits must stay one-to-one and in the same order — the
    contract enforces it — so both are built from the same range.
    """
    refs = [f"P-{index}" for index in range(candidates)]
    presentations = [
        {
            "place_key": {"source": "kto", "ref": ref},
            "title": f"장소 {ref}",
            "summary": "테스트 장소입니다.",
            "kind_id": "cafe",
            "kind_label": "카페",
            "distance_m": 100 + index,
            "address": "서울 어딘가",
            "core_items": [],
            "promoted_items": [],
            "detail_items": [],
            "notices": [],
            "why_matched": [],
        }
        for index, ref in enumerate(refs)
    ]
    hits = [
        {"place": {"key": {"source": "kto", "ref": ref}, "lat": 37.55, "lng": 126.92}}
        for ref in refs
    ]
    return _DiscoveryResponse.model_validate(
        {
            "contract_version": "place-discovery-v1",
            "planning": {
                "contract_version": "place-discovery-planning-v1",
                "status": "ready",
                "source_disposition": "literal_target",
                "resolution": "single_interpretation",
                "lenses": {"target_lenses": [], "signal_lenses": []},
                "issues": [],
            },
            "lens_results": [
                {
                    "lens_id": "purpose.dining",
                    "display_label": "카페/식당",
                    "support_note": "카페 목적으로 좁혔어요.",
                    "search": {"groups": [{"results": hits}]},
                    "presentations": presentations,
                }
            ],
            "notices": [
                {"code": f"place.extra_{index}", "message": f"참고 {index}", "lens_id": None}
                for index in range(extra_notices)
            ],
        }
    )


# --------------------------------------------------------- named region — Option B


@pytest.mark.parametrize("case_id", ["named_region_place", "named_region_hospital"])
async def test_named_region_uses_device_coordinates_and_never_resolves_the_region(
    case_id: str,
) -> None:
    """Option B (D-051): the region is not a location value.

    Place still runs — the user did ask for places — but around the trusted device
    coordinates, and nothing derives a coordinate from the words. Honouring the region
    needs a geocoder Place does not have; a separate card owns that and the explicit
    named-region CLARIFY that would go with it.
    """
    service, _, adapters, case = _run_case(case_id)
    await service.run(query=case["query"], principal=PRINCIPAL, context=dict(LOCATION))
    [request] = adapters[CapabilityName.PLACE].calls
    # The device's coordinates, unchanged — not Seongsu-dong's, not Haeundae's.
    assert (request.payload.lat, request.payload.lon) == (37.5665, 126.978)
    # The region survives only inside the preserved query text, where Place's own
    # proposer reads it as words and never as a place to search.
    assert request.payload.query == case["query"]


def test_the_router_prompt_never_receives_coordinates() -> None:
    """The classifier cannot leak a location it was never shown — including when the
    query names one.

    Asserted on the values and on the metadata line rather than on the word
    "location", which legitimately appears in the policy text that forbids treating a
    named area as one.
    """
    prompt = build_semantic_router_prompt(
        query="성수동에서 산책하기 좋은 곳 찾아줘",
        context={**LOCATION, "source": "assistant"},
    )
    assert "37.5665" not in prompt and "126.978" not in prompt
    metadata_line = next(
        line for line in prompt.splitlines() if line.startswith("ROUTING_METADATA:")
    )
    assert json.loads(metadata_line[len("ROUTING_METADATA:") :]) == {"source": "assistant"}


def test_place_projection_always_discloses_the_search_frame() -> None:
    """Option B's two required disclosures, on every Place result.

    The user must be able to tell (a) which area was actually searched and (b) that a
    named region in their question was not applied. Both are stated unconditionally:
    deciding *whether* a region was named would need the router classification this
    card deferred, and a disclosure that appears only sometimes is one nobody can rely
    on. The wording is true either way and never claims a region was resolved.
    """
    data = project_place_capability_data(_discovery(candidates=1))
    assert data["answer"].startswith("현재 기기 위치를 기준으로")

    frame = data["notices"][0]
    assert frame["code"] == "place.searched_around_current_location"
    assert frame["lens_id"] is None
    assert "현재 기기 위치를 기준으로" in frame["message"]
    assert "반영하지 않았습니다" in frame["message"]
    # Never claims the region was resolved, geocoded, or used.
    for forbidden in ("지오코딩", "좌표로 변환", "그 지역에서 찾았"):
        assert forbidden not in frame["message"]


def test_search_frame_notice_survives_a_noisy_result() -> None:
    """The one notice that may never be dropped.

    A result carrying more notices than the projection budget must still say where it
    searched — otherwise the disclosure disappears exactly when the answer is most
    crowded.
    """
    data = project_place_capability_data(_discovery(candidates=1, extra_notices=20))
    codes = [notice["code"] for notice in data["notices"]]
    assert codes[0] == "place.searched_around_current_location"
    assert len(codes) == len(set(codes))


def test_place_abstention_still_discloses_where_it_searched() -> None:
    """Finding nothing is still a search, and the frame still applies."""
    data = project_place_capability_data(_discovery(candidates=0))
    assert data["answer"].startswith("현재 기기 위치를 기준으로")
    assert data["notices"][0]["code"] == "place.searched_around_current_location"


# ---------------------------------------------------------------- mixed aggregation


def _place_data(
    answer: str = "현재 기기 위치를 기준으로 1가지 방향에서 가까운 장소 2곳을 찾았습니다.",
) -> dict:
    return {
        "contract_version": "place-capability-v1",
        "answer": answer,
        "interpretation_summary": {
            "status": "ready",
            "disposition": "literal_target",
            "resolution": "single_interpretation",
            "message": "미용/위탁 방향으로 나눠 찾았어요.",
        },
        "groups": [],
        "refinements": [],
        "notices": [],
    }


class ScriptedAdapter:
    """An adapter that returns a prepared result, for aggregation shapes."""

    def __init__(self, capability: CapabilityName, result: CapabilityResult) -> None:
        self.capability = capability
        self._result = result
        self.calls: list[CapabilityRequest] = []

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        self.calls.append(request)
        return self._result


async def _run_mixed(place: CapabilityResult, walk: CapabilityResult):
    adapters = {name: FakeAdapter(name) for name in CapabilityName}
    adapters[CapabilityName.PLACE] = ScriptedAdapter(CapabilityName.PLACE, place)
    adapters[CapabilityName.WALK] = ScriptedAdapter(CapabilityName.WALK, walk)
    transport = ScriptedTransport(json.dumps({"execute": ["place", "walk"], "handoffs": []}))
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine(adapters),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    return await service.run(
        query="오늘 산책하기 좋은 곳 추천해줘", principal=PRINCIPAL, context=dict(LOCATION)
    )


_WALK_OK = CapabilityResult(
    capability=CapabilityName.WALK,
    status=CapabilityStatus.OK,
    data={"now": {"grade": "GOOD", "axes": {}}},
    elapsed_ms=5,
)


async def test_place_ok_plus_walk_ok_is_answered_with_both_labelled_sections() -> None:
    response = await _run_mixed(
        CapabilityResult(
            capability=CapabilityName.PLACE,
            status=CapabilityStatus.OK,
            data=_place_data(),
            elapsed_ms=5,
        ),
        _WALK_OK,
    )
    assert response.status == AssistantStatus.ANSWERED
    assert "[장소]" in response.message and "[산책]" in response.message
    assert "현재 기기 위치를 기준으로" in response.message
    # Both structured payloads survive for the client to render.
    by_capability = {result.capability: result for result in response.results}
    assert by_capability[CapabilityName.PLACE].data is not None
    assert by_capability[CapabilityName.WALK].data is not None


async def test_place_abstained_plus_walk_ok_is_partial_and_keeps_the_walk_verdict() -> None:
    response = await _run_mixed(
        CapabilityResult(
            capability=CapabilityName.PLACE,
            status=CapabilityStatus.ABSTAINED,
            data=_place_data("현재 기기 위치를 기준으로 찾아봤지만 주변 장소를 찾지 못했어요."),
            abstention=OutcomeDetail(
                code="place_no_candidates",
                message="현재 기기 위치를 기준으로 찾아봤지만 주변 장소를 찾지 못했어요.",
            ),
            elapsed_ms=5,
        ),
        _WALK_OK,
    )
    assert response.status == AssistantStatus.PARTIAL
    by_capability = {result.capability: result for result in response.results}
    assert by_capability[CapabilityName.WALK].data == {"now": {"grade": "GOOD", "axes": {}}}
    # An abstaining Place still carries its data — the client can show the
    # interpretation and any refinements even with zero candidates.
    assert by_capability[CapabilityName.PLACE].data is not None


async def test_place_error_plus_walk_ok_is_partial_without_a_silent_fallback() -> None:
    response = await _run_mixed(
        CapabilityResult(
            capability=CapabilityName.PLACE,
            status=CapabilityStatus.ERROR,
            error=ErrorDetail(
                kind="place_discovery_unavailable", detail="장소 추천 기능에 연결할 수 없습니다."
            ),
            elapsed_ms=5,
        ),
        _WALK_OK,
    )
    assert response.status == AssistantStatus.PARTIAL
    assert "장소 추천 기능에 연결할 수 없습니다." in response.message
    by_capability = {result.capability: result for result in response.results}
    assert by_capability[CapabilityName.PLACE].data is None
    assert by_capability[CapabilityName.WALK].status is CapabilityStatus.OK


async def test_both_abstained_is_uncertain_not_failed() -> None:
    response = await _run_mixed(
        CapabilityResult(
            capability=CapabilityName.PLACE,
            status=CapabilityStatus.ABSTAINED,
            data=_place_data("현재 기기 위치를 기준으로 찾아봤지만 주변 장소를 찾지 못했어요."),
            abstention=OutcomeDetail(code="place_no_candidates", message="못 찾았어요."),
            elapsed_ms=5,
        ),
        CapabilityResult(
            capability=CapabilityName.WALK,
            status=CapabilityStatus.ABSTAINED,
            data={"now": {"grade": "unknown"}},
            abstention=OutcomeDetail(
                code="unknown_verdict", message="현재 관측 자료만으로 산책 조건을 판단할 수 없습니다."
            ),
            elapsed_ms=5,
        ),
    )
    assert response.status == AssistantStatus.UNCERTAIN
