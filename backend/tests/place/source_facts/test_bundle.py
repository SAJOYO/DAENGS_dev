from daengs_place.place.source_facts.bundle import (
    SourceFactKey,
    SourceFactVariant,
    build_candidate_fact_bundle,
)
from daengs_place.place.source_facts.kcisa import project_kcisa
from daengs_place.place.source_facts.states import DetailAcquisitionState


def _row(category: str = "카페", **changes) -> dict:
    return {
        "시설명": "후보",
        "카테고리1": "반려동물업",
        "카테고리2": "반려동물식당카페",
        "카테고리3": category,
        "반려동물 동반 가능정보": "Y",
        "반려동물 전용 정보": "해당없음",
        "입장 가능 동물 크기": "모두 가능",
        "반려동물 제한사항": "없음",
        "장소(실내) 여부": "Y",
        "장소(실외)여부": "N",
        "애견 동반 추가 요금": "없음",
        **changes,
    }


def _variant(
    record_ref: str,
    row: dict,
    occurrence_count: int = 1,
    *,
    source_ref: str = "same-place",
) -> SourceFactVariant:
    return SourceFactVariant(
        source_ref=source_ref,
        record_ref=record_ref,
        occurrence_count=occurrence_count,
        snapshot="test-snapshot",
        detail_state=DetailAcquisitionState.NOT_APPLICABLE,
        projection=project_kcisa(row),
    )


def test_bundle_keeps_variants_without_inventing_a_representative() -> None:
    key = SourceFactKey(source="kcisa", source_ref="same-place")
    first = _variant("record:b", _row(시설명="이름 B"), 2)
    second = _variant("record:a", _row(시설명="이름 A"), 3)

    bundle = build_candidate_fact_bundle(key, [first, second])

    assert [variant.record_ref for variant in bundle.variants] == ["record:a", "record:b"]
    assert bundle.conflicts == ()
    assert bundle.availability == "present"
    assert bundle.projection_state == "complete"
    assert bundle.has_conflicts is False
    assert bundle.physical_occurrences == 5


def test_bundle_exposes_section_conflict_and_preserves_both_values() -> None:
    key = SourceFactKey(source="kcisa", source_ref="same-place")
    cafe = _variant("record:cafe", _row("카페"))
    museum = _variant(
        "record:museum",
        _row("박물관", 카테고리2="반려동반여행"),
    )

    bundle = build_candidate_fact_bundle(key, [museum, cafe])

    purpose_conflict = next(item for item in bundle.conflicts if item.section == "purpose")
    assert bundle.availability == "present"
    assert bundle.projection_state == "complete"
    assert bundle.has_conflicts is True
    assert {variant.projection.purpose.primary for variant in bundle.variants} == {
        "cafe",
        "museum",
    }
    assert {ref for group in purpose_conflict.groups for ref in group.record_refs} == {
        "record:cafe",
        "record:museum",
    }


def test_bundle_distinguishes_missing_and_partial() -> None:
    key = SourceFactKey(source="kcisa", source_ref="candidate")

    missing = build_candidate_fact_bundle(key, [])
    partial = build_candidate_fact_bundle(
        key,
        [_variant("record:unknown", _row("새로운 분류"), source_ref="candidate")],
    )

    assert missing.availability == "missing"
    assert missing.projection_state is None
    assert missing.acquisition_states == ()
    assert missing.physical_occurrences == 0
    assert partial.availability == "present"
    assert partial.projection_state == "partial"
    assert partial.variants[0].projection.issues[0].code == "unmapped_purpose"


def test_conflict_does_not_hide_partial_projection_state() -> None:
    key = SourceFactKey(source="kcisa", source_ref="same-place")
    bundle = build_candidate_fact_bundle(
        key,
        [
            _variant("record:cafe", _row("카페")),
            _variant("record:unknown", _row("새로운 분류")),
        ],
    )

    assert bundle.has_conflicts is True
    assert bundle.projection_state == "partial"


def test_bundle_rejects_a_variant_from_another_candidate() -> None:
    key = SourceFactKey(source="kcisa", source_ref="candidate-a")
    wrong = _variant("record:b", _row(), source_ref="candidate-b")

    try:
        build_candidate_fact_bundle(key, [wrong])
    except ValueError as exc:
        assert "source_refs must match" in str(exc)
    else:
        raise AssertionError("variant from another candidate was accepted")
