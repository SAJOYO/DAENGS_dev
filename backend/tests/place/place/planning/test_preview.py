import pytest
from pydantic import ValidationError

from daengs_place.place.contracts import (
    PlaceClassification,
    PlaceFacts,
    PlaceMatch,
    PlaceRef,
    PlaceResult,
)
from daengs_place.place.planning.compiler import build_place_search_plan
from daengs_place.place.planning.contract import PlaceKind
from daengs_place.place.planning.preview import PreviewCandidate, build_plan_preview
from daengs_place.place.source_facts.bundle import (
    SourceFactKey,
    SourceFactVariant,
    build_candidate_fact_bundle,
)
from daengs_place.place.source_facts.kcisa import project_kcisa
from daengs_place.place.source_facts.kto import project_kto
from daengs_place.place.source_facts.states import DetailAcquisitionState


def _place(source: str, ref: str, kind: PlaceKind, parking: bool | None) -> PlaceResult:
    key = PlaceRef(source=source, ref=ref)
    return PlaceResult(
        key=key,
        name=ref,
        lat=37.5,
        lng=127.0,
        distance_m=100,
        match=PlaceMatch(source=key, kind=kind.value),
        classifications=[
            PlaceClassification(
                source=key,
                source_category=kind.value,
                kind=kind.value,
                mapping_version="test/1",
            )
        ],
        facts=PlaceFacts(parking=parking),
    )


def _kcisa_bundle(ref: str):
    projection = project_kcisa(
        {
            "카테고리1": "반려동물업",
            "카테고리2": "반려동물식당카페",
            "카테고리3": "카페",
            "반려동물 동반 가능정보": "Y",
            "반려동물 전용 정보": "해당없음",
            "입장 가능 동물 크기": "모두 가능",
            "반려동물 제한사항": "없음",
            "주차 가능여부": "Y",
        }
    )
    return build_candidate_fact_bundle(
        SourceFactKey(source="kcisa", source_ref=ref),
        [
            SourceFactVariant(
                source_ref=ref,
                record_ref=f"record:{ref}",
                occurrence_count=1,
                snapshot="test",
                detail_state=DetailAcquisitionState.NOT_APPLICABLE,
                projection=projection,
            )
        ],
    )


def _kto_bundle(ref: str):
    projection = project_kto({"contenttypeid": "38"})
    return build_candidate_fact_bundle(
        SourceFactKey(source="kto", source_ref=ref),
        [
            SourceFactVariant(
                source_ref=ref,
                record_ref=ref,
                occurrence_count=1,
                snapshot="test",
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                projection=projection,
            )
        ],
    )


def test_preview_separates_executor_outcomes_from_source_evidence() -> None:
    plan = build_place_search_plan(
        lat=37.5,
        lng=127.0,
        radius_m=3000,
        kinds=[PlaceKind.CAFE, PlaceKind.SHOPPING],
        limit_per_kind=20,
        prefer_parking=True,
    )
    candidates = [
        PreviewCandidate(
            place=_place("kcisa", "known", PlaceKind.CAFE, True),
            bundle=_kcisa_bundle("known"),
        ),
        PreviewCandidate(
            place=_place("kcisa", "missing", PlaceKind.CAFE, False),
        ),
        PreviewCandidate(
            place=_place("kto", "effective", PlaceKind.SHOPPING, None),
            bundle=_kto_bundle("effective"),
        ),
    ]

    preview = build_plan_preview(plan, candidates, candidate_limit_per_kind=500)

    assert preview.initial_candidates == 3
    purpose, parking = preview.gates
    assert purpose.model_dump(exclude={"source_evidence"}) == {
        "capability_id": "purpose.kind",
        "mode": "filter",
        "input_candidates": 3,
        "known_match": 3,
        "known_mismatch": 0,
        "unknown": 0,
        "remaining": 3,
    }
    assert purpose.source_evidence.model_dump() == {
        "known": 2,
        "unknown": 0,
        "missing": 1,
        "conflicted": 0,
        "failed": 0,
        "unsupported": 0,
        "acquisition_states": {"not_applicable": 1, "not_fetched": 1},
    }
    assert parking.model_dump(exclude={"source_evidence"}) == {
        "capability_id": "operations.parking",
        "mode": "prefer",
        "input_candidates": 3,
        "known_match": 1,
        "known_mismatch": 1,
        "unknown": 1,
        "remaining": 3,
    }
    assert parking.source_evidence.model_dump() == {
        "known": 1,
        "unknown": 0,
        "missing": 1,
        "conflicted": 0,
        "failed": 0,
        "unsupported": 1,
        "acquisition_states": {"not_applicable": 1, "not_fetched": 1},
    }


def test_preview_rejects_a_bundle_from_another_candidate() -> None:
    with pytest.raises(ValidationError, match="bundle must match"):
        PreviewCandidate(
            place=_place("kcisa", "candidate-a", PlaceKind.CAFE, True),
            bundle=_kcisa_bundle("candidate-b"),
        )


def test_preview_surfaces_conflict_without_overwriting_the_execution_value() -> None:
    ref = "conflicted"
    variants = []
    for index, parking in enumerate(("Y", "N")):
        projection = project_kcisa(
            {
                "카테고리3": "카페",
                "반려동물 동반 가능정보": "Y",
                "주차 가능여부": parking,
            }
        )
        variants.append(
            SourceFactVariant(
                source_ref=ref,
                record_ref=f"record:{index}",
                occurrence_count=1,
                snapshot="test",
                detail_state=DetailAcquisitionState.NOT_APPLICABLE,
                projection=projection,
            )
        )
    bundle = build_candidate_fact_bundle(
        SourceFactKey(source="kcisa", source_ref=ref),
        variants,
    )
    plan = build_place_search_plan(
        lat=37.5,
        lng=127.0,
        radius_m=3000,
        kinds=[PlaceKind.CAFE],
        limit_per_kind=20,
        prefer_parking=True,
    )

    preview = build_plan_preview(
        plan,
        [
            PreviewCandidate(
                place=_place("kcisa", ref, PlaceKind.CAFE, True),
                bundle=bundle,
            )
        ],
        candidate_limit_per_kind=1000,
    )

    parking = preview.gates[1]
    assert parking.known_match == 1
    assert parking.source_evidence.conflicted == 1
    assert parking.source_evidence.known == 0


def test_unrelated_partial_projection_does_not_hide_known_capability_evidence() -> None:
    ref = "partial-other-section"
    projection = project_kcisa(
        {
            "카테고리3": "카페",
            "반려동물 동반 가능정보": "Y",
            "반려동물 제한사항": "실외만 동반 가능",
            "장소(실내) 여부": "Y",
            "주차 가능여부": "Y",
        }
    )
    assert projection.state.value == "partial"
    bundle = build_candidate_fact_bundle(
        SourceFactKey(source="kcisa", source_ref=ref),
        [
            SourceFactVariant(
                source_ref=ref,
                record_ref="record:partial",
                occurrence_count=1,
                snapshot="test",
                detail_state=DetailAcquisitionState.NOT_APPLICABLE,
                projection=projection,
            )
        ],
    )
    plan = build_place_search_plan(
        lat=37.5,
        lng=127.0,
        radius_m=3000,
        kinds=[PlaceKind.CAFE],
        limit_per_kind=20,
        prefer_parking=True,
    )

    preview = build_plan_preview(
        plan,
        [
            PreviewCandidate(
                place=_place("kcisa", ref, PlaceKind.CAFE, True),
                bundle=bundle,
            )
        ],
        candidate_limit_per_kind=1000,
    )

    assert preview.gates[1].source_evidence.known == 1
    assert preview.gates[1].source_evidence.unknown == 0
