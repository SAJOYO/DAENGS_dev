"""Authoritative Android-facing `AssistantResponse` fixtures for the Place capability.

These are the JSON bodies a client actually receives from `POST /assistant/query` when
Place runs. They exist because the Android client is built in a separate repository and
a separate session: it needs the real wire shape to write its parser against, and a
hand-typed approximation of that shape is how two repositories quietly disagree.

**Nothing here is hand-shaped.** The Place half goes through the production
`project_place_capability_data`, the Walk half through the production `WalkOut` DTO
dumped exactly as `WalkCapabilityAdapter` dumps it, and the envelope through the
production `aggregate_results`. Only the *inputs* are authored — the upstream discovery
payload and the walk verdict — and both are validated by the same models the runtime
validates them with, so an input that could not occur cannot produce a fixture.

Regenerate with:

    uv run python -m daengs_evals.place_fixtures --write

`tests/test_place_capability_fixtures.py` rebuilds them and asserts the committed files
match byte for byte, so a contract change that moves the wire shape fails there rather
than silently in the Android repository weeks later.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from daengs_backend.orchestration.adapters._place_contract import _DiscoveryResponse
from daengs_backend.orchestration.adapters._place_projection import (
    project_place_capability_data,
)
from daengs_backend.orchestration.aggregate import aggregate_results
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    OutcomeDetail,
    PlacePayload,
    RoutePlan,
    RouterKind,
    WalkPayload,
)
from daengs_evals import BACKEND_DIR

FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures" / "place_capability"
CONTRACT_VERSION = "place-capability-v1"

# A fixed instant so regeneration is byte-stable. Walk's DTO carries timestamps, and a
# fixture that changes on every run is a fixture nobody can diff.
_AT = datetime(2026, 9, 4, 9, 0, tzinfo=UTC) + timedelta(hours=9)
_LAT = 37.5563
_LON = 126.9236


# --------------------------------------------------------------- upstream input builders


def _fact(
    fact_id: str,
    label: str,
    text: str,
    *,
    source_state: str = "known",
    evaluation_state: str = "not_evaluated",
    severity: str = "info",
    source: str = "kto",
    ref: str = "src-1",
    role: str = "primary",
    origin: str = "own",
    link: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "label": label,
        "display_text": text,
        "source_state": source_state,
        "evaluation_state": evaluation_state,
        "severity": severity,
        "provenance": {
            "source": {"source": source, "ref": ref},
            "source_role": role,
            "value_origin": origin,
            "link": link,
        },
    }


def _presentation(ref: str, title: str, **kwargs: Any) -> dict[str, Any]:
    return {
        "place_key": {"source": "kto", "ref": ref},
        "title": title,
        "summary": kwargs.get("summary", "반려견과 함께 갈 수 있는 곳이에요."),
        "kind_id": kwargs.get("kind_id", "grooming"),
        "kind_label": kwargs.get("kind_label", "미용"),
        "distance_m": kwargs.get("distance_m", 420),
        "address": kwargs.get("address", "서울 마포구 양화로 1"),
        "core_items": kwargs.get("core", []),
        "promoted_items": kwargs.get("promoted", []),
        "detail_items": kwargs.get("detail", []),
        "notices": kwargs.get("notices", []),
        "why_matched": kwargs.get("why", []),
    }


def _lens(
    lens_id: str, label: str, support_note: str, presentations: list[dict[str, Any]]
) -> dict[str, Any]:
    hits = [
        {"place": {"key": item["place_key"], "lat": _LAT, "lng": _LON}} for item in presentations
    ]
    return {
        "lens_id": lens_id,
        "display_label": label,
        "support_note": support_note,
        "search": {"groups": [{"results": hits}]},
        "presentations": presentations,
    }


def _discovery(
    status: str,
    lens_results: list[dict[str, Any]],
    *,
    notices: list[dict[str, Any]] | None = None,
    signal_lenses: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> _DiscoveryResponse:
    return _DiscoveryResponse.model_validate(
        {
            "contract_version": "place-discovery-v1",
            "planning": {
                "contract_version": "place-discovery-planning-v1",
                "status": status,
                "source_disposition": "literal_target",
                "resolution": "single_interpretation",
                "lenses": {"target_lenses": [], "signal_lenses": signal_lenses or []},
                "issues": issues or [],
            },
            "lens_results": lens_results,
            "notices": notices or [],
        }
    )


def _walk_data(grade: str = "GOOD") -> dict[str, Any]:
    """The Walk half, through the real DTO and the adapter's exact dump call."""
    from daengs_life.app.dto.walk import (
        AxisOut,
        LocationOut,
        SourceOut,
        TimelinePoint,
        VerdictOut,
        WalkOut,
        WindowOut,
    )

    axes = {
        "heat": AxisOut(grade=grade, note="기온은 산책하기 알맞아요."),
        "air": AxisOut(grade=grade, note="미세먼지 농도가 낮아요."),
        "rain": AxisOut(grade=grade, note="비 소식이 없어요."),
    }
    walk_out = WalkOut(
        location=LocationOut(
            dong="서교동",
            grid=(59, 126),
            air_station="마포",
            air_station_km=1.2,
            aws_station="서울",
            warning_zone="서울특별시",
            label="서교동 (측정소: 마포) 기준",
        ),
        generated_at=_AT,
        now=VerdictOut(
            at=_AT, grade=grade, dominant=[], axes=axes, unknown_axes=[], capped=False
        ),
        timeline=[TimelinePoint(at=_AT + timedelta(hours=n), grade=grade) for n in range(3)],
        windows=[WindowOut(**{"from": _AT, "to": _AT + timedelta(hours=2), "grade": grade})],
        sources=[SourceOut(provider="kma", ok=True), SourceOut(provider="airkorea", ok=True)],
        notes=[],
    )
    # Byte-identical to what WalkCapabilityAdapter.run() puts in CapabilityResult.data.
    return walk_out.model_dump(mode="json", by_alias=True)


# ------------------------------------------------------------------------ result helpers


def _place_request(query: str) -> CapabilityRequest:
    return CapabilityRequest(
        capability=CapabilityName.PLACE,
        payload=PlacePayload(query=query, lat=_LAT, lon=_LON),
    )


def _walk_request() -> CapabilityRequest:
    return CapabilityRequest(
        capability=CapabilityName.WALK, payload=WalkPayload(lat=_LAT, lon=_LON)
    )


def _place_ok(data: dict[str, Any]) -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.PLACE, status=CapabilityStatus.OK, data=data, elapsed_ms=2_140
    )


def _place_abstained(data: dict[str, Any], code: str) -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.PLACE,
        status=CapabilityStatus.ABSTAINED,
        data=data,
        abstention=OutcomeDetail(code=code, message=data["answer"]),
        elapsed_ms=1_980,
    )


def _walk_ok() -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.WALK,
        status=CapabilityStatus.OK,
        data=_walk_data(),
        elapsed_ms=310,
    )


def _plan(*requests: CapabilityRequest) -> RoutePlan:
    return RoutePlan(requests=list(requests), router=RouterKind.LLM)


def _two_candidate_discovery() -> _DiscoveryResponse:
    return _discovery(
        "ready",
        [
            _lens(
                "purpose.pet_care",
                "미용/위탁",
                "미용·위탁 목적으로 좁혔어요.",
                [
                    _presentation(
                        "P-1001",
                        "멍멍이 미용실 홍대점",
                        summary="반려견 전용 미용실이에요.",
                        promoted=[
                            _fact(
                                "pet_access.allowed",
                                "반려동물 출입",
                                "반려동물 동반이 가능해요.",
                                evaluation_state="compatible",
                            ),
                            _fact(
                                "operations.parking",
                                "주차",
                                "건물 내 주차장을 이용할 수 있어요.",
                                source="kcisa",
                                ref="src-9",
                                role="supporting",
                                origin="borrowed",
                                link={"state": "verified"},
                            ),
                        ],
                        why=[{"code": "kind_match", "message": "요청하신 미용 목적과 일치해요."}],
                    ),
                    _presentation(
                        "P-1002",
                        "댕댕 펫살롱",
                        summary="예약제로 운영되는 소형견 전문 미용실이에요.",
                        distance_m=610,
                        address="서울 마포구 동교로 15",
                        promoted=[
                            # An unknown fact stays unknown — never rendered as a negative.
                            _fact(
                                "pet_access.allowed",
                                "반려동물 출입",
                                "확인되지 않았어요.",
                                source_state="not_fetched",
                                evaluation_state="unknown",
                                severity="warning",
                            ),
                            # Borrowed from a different source record whose link is only a
                            # candidate — a client must not present this as confirmed.
                            _fact(
                                "operations.hours",
                                "영업시간",
                                "10:00~20:00 (다른 기록에서 가져온 정보예요.)",
                                source="kcisa",
                                ref="src-12",
                                role="supporting",
                                origin="borrowed",
                                link={"state": "candidate"},
                            ),
                        ],
                        notices=[
                            {
                                "code": "candidate.verify_before_visit",
                                "message": "방문 전에 전화로 확인해 주세요.",
                                "severity": "warning",
                            }
                        ],
                        why=[{"code": "kind_match", "message": "요청하신 미용 목적과 일치해요."}],
                    ),
                ],
            )
        ],
    )


# ------------------------------------------------------------------------- the fixtures


def build_fixtures() -> dict[str, AssistantResponse]:
    """Every fixture, built through the production projection/aggregation path."""
    fixtures: dict[str, AssistantResponse] = {}

    # 1. Place only, succeeded.
    query = "근처 미용실 찾아줘"
    fixtures["place_success"] = aggregate_results(
        request_id="fx-place-success-0001",
        route_plan=_plan(_place_request(query)),
        results=[_place_ok(project_place_capability_data(_two_candidate_discovery()))],
    )

    # 2. Place + Walk. Requests follow the planner's canonical order (walk, place).
    mixed_query = "오늘 산책하기 좋은 곳 추천해줘"
    outing = project_place_capability_data(
        _discovery(
            "ready",
            [
                _lens(
                    "purpose.outing",
                    "여행지/레저",
                    "산책 삼아 들를 만한 나들이 장소로 찾았어요.",
                    [
                        _presentation(
                            "P-2001",
                            "선유도공원",
                            summary="한강변에 자리한 생태공원이에요.",
                            kind_id="leisure",
                            kind_label="레저",
                            distance_m=850,
                            address="서울 영등포구 선유로 343",
                            promoted=[
                                _fact(
                                    "pet_access.allowed",
                                    "반려동물 출입",
                                    "목줄 착용 시 동반이 가능해요.",
                                    evaluation_state="conditional",
                                )
                            ],
                            why=[
                                {"code": "kind_match", "message": "나들이 목적과 가까운 장소예요."}
                            ],
                        )
                    ],
                )
            ],
        )
    )
    fixtures["place_plus_walk_success"] = aggregate_results(
        request_id="fx-place-walk-0002",
        route_plan=_plan(_walk_request(), _place_request(mixed_query)),
        results=[_walk_ok(), _place_ok(outing)],
    )

    # 3. Place abstained — outside what Place data can answer at all.
    unsupported = project_place_capability_data(
        _discovery(
            "unsupported",
            [],
            issues=[{"code": "unsupported_semantic_intent", "blocking": True}],
        )
    )
    fixtures["place_abstention"] = aggregate_results(
        request_id="fx-place-abstain-0003",
        route_plan=_plan(_place_request("용이 나오는 곳 찾아줘")),
        results=[_place_abstained(unsupported, "place_unsupported")],
    )

    # 4. Partial: Place answered, Walk failed. No silent fallback (O-10).
    fixtures["partial_success"] = aggregate_results(
        request_id="fx-partial-0004",
        route_plan=_plan(_walk_request(), _place_request(mixed_query)),
        results=[
            CapabilityResult(
                capability=CapabilityName.WALK,
                status=CapabilityStatus.ERROR,
                error=ErrorDetail(
                    kind="walk_provider_unavailable",
                    detail="산책 조건 서비스에 연결할 수 없습니다.",
                ),
                elapsed_ms=180,
            ),
            _place_ok(project_place_capability_data(_two_candidate_discovery())),
        ],
    )

    # 5. Missing location: exclusive CLARIFY, nothing executed.
    fixtures["missing_location"] = aggregate_results(
        request_id="fx-clarify-location-0005",
        route_plan=RoutePlan.model_validate(
            {
                "requests": [],
                "handoffs": [],
                "clarify": {
                    "question": "장소를 찾을 위치의 위도와 경도를 알려주세요.",
                    "missing": ["location.lat", "location.lon"],
                },
                "router": "llm",
                "model": None,
            }
        ),
        results=[],
    )

    # 6. Searched and found nothing.
    empty = project_place_capability_data(
        _discovery(
            "ready",
            [_lens("purpose.healthcare", "병원/약국", "주변에서 병원·약국을 찾아봤어요.", [])],
            notices=[
                {
                    "code": "place.no_candidates_in_radius",
                    "message": "반경 안에서 조건에 맞는 장소를 찾지 못했어요.",
                    "lens_id": "purpose.healthcare",
                }
            ],
        )
    )
    fixtures["no_candidates"] = aggregate_results(
        request_id="fx-no-candidates-0006",
        route_plan=_plan(_place_request("근처 동물병원 찾아줘")),
        results=[_place_abstained(empty, "place_no_candidates")],
    )

    # 7. A required choice the user must make before Place can search.
    refine = project_place_capability_data(
        _discovery(
            "needs_clarification",
            [],
            signal_lenses=[
                {
                    "lens_id": "signal.dog_size",
                    "display_label": "반려견 크기",
                    "availability": "needs_selection",
                    "required": True,
                    "support_note": "크기에 따라 입장 가능 여부가 달라져요.",
                    "options": [
                        {
                            "option_id": "small",
                            "display_label": "소형견",
                            "availability": "available",
                            "support_note": "10kg 이하",
                        },
                        {
                            "option_id": "large",
                            "display_label": "대형견",
                            "availability": "available",
                            "support_note": "10kg 초과",
                        },
                    ],
                }
            ],
        )
    )
    fixtures["refinement_required"] = aggregate_results(
        request_id="fx-refinement-0007",
        route_plan=_plan(_place_request("우리 개 데리고 갈 곳 찾아줘")),
        results=[_place_abstained(refine, "place_needs_clarification")],
    )

    return fixtures


def serialize(response: AssistantResponse) -> str:
    return json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", required=True)
    parser.parse_args()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, response in build_fixtures().items():
        (FIXTURE_DIR / f"{name}.json").write_text(serialize(response), encoding="utf-8")
        print(f"wrote {FIXTURE_DIR / f'{name}.json'}")


if __name__ == "__main__":
    main()


__all__ = ["CONTRACT_VERSION", "FIXTURE_DIR", "build_fixtures", "serialize"]
