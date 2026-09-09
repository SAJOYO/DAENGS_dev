import pytest

from daengs_place.place.contracts import (
    FieldProvenance,
    PlaceClassification,
    PlaceFacts,
    PlaceMatch,
    PlaceRef,
    PlaceResult,
)
from daengs_place.place.evaluations import DogAccessEvaluation
from daengs_place.place.presentation.assembler import assemble_place_presentation
from daengs_place.place.presentation.contract import EvaluationState, PresentationFactId
from daengs_place.place.presentation.needs import InformationNeedId
from daengs_place.place.search import PlaceEvaluations, PlaceSearchGroup, PlaceSearchHit
from daengs_place.place.source_facts.bundle import (
    CandidateFactBundle,
    SourceFactKey,
    SourceFactVariant,
    build_candidate_fact_bundle,
)
from daengs_place.place.source_facts.kcisa import project_kcisa
from daengs_place.place.source_facts.kto import project_kto
from daengs_place.place.source_facts.states import DetailAcquisitionState, FactState


def _hit(
    *,
    source: str = "kto",
    ref: str = "K1",
    facts: PlaceFacts | None = None,
    field_sources: dict[str, FieldProvenance] | None = None,
    evaluations: PlaceEvaluations | None = None,
) -> PlaceSearchHit:
    key = PlaceRef(source=source, ref=ref)
    return PlaceSearchHit(
        place=PlaceResult(
            key=key,
            name="테스트 장소",
            lat=37.5,
            lng=126.9,
            distance_m=850,
            match=PlaceMatch(source=key, kind="travel"),
            classifications=[
                PlaceClassification(
                    source=key,
                    source_category="12",
                    kind="travel",
                    mapping_version="test-v1",
                )
            ],
            facts=facts or PlaceFacts(address="서울 마포구"),
            field_sources=field_sources or {},
        ),
        evaluations=evaluations or PlaceEvaluations(),
    )


def _group(hit: PlaceSearchHit) -> PlaceSearchGroup:
    return PlaceSearchGroup(kind="travel", limit=10, results=[hit])


def _kto_bundle(detail: dict | None, *, detail_state: FactState) -> CandidateFactBundle:
    key = SourceFactKey(source="kto", source_ref="K1")
    return build_candidate_fact_bundle(
        key,
        [
            SourceFactVariant(
                source_ref="K1",
                record_ref="record:1",
                occurrence_count=1,
                snapshot="test-snapshot",
                detail_state=DetailAcquisitionState.FETCHED,
                projection=project_kto(
                    {"contenttypeid": "12"},
                    detail,
                    detail_state=detail_state,
                ),
            )
        ],
    )


def _kcisa_bundle(size: str) -> CandidateFactBundle:
    key = SourceFactKey(source="kcisa", source_ref="C1")
    return build_candidate_fact_bundle(
        key,
        [
            SourceFactVariant(
                source_ref="C1",
                record_ref="record:1",
                occurrence_count=1,
                snapshot="test-snapshot",
                detail_state=DetailAcquisitionState.NOT_APPLICABLE,
                projection=project_kcisa(
                    {
                        "카테고리3": "자연관광지",
                        "반려동물 동반 가능정보": "Y",
                        "입장 가능 동물 크기": size,
                    }
                ),
            )
        ],
    )


def test_activity_need_promotes_same_source_amenity_fact() -> None:
    hit = _hit()
    bundle = _kto_bundle(
        {
            "acmpyTypeCd": "일부구역 동반가능",
            "relaPosesFclty": "반려견 놀이터",
        },
        detail_state=FactState.KNOWN,
    )

    result = assemble_place_presentation(
        hit,
        _group(hit),
        lens_id="lens:play",
        lens_label="#놀기",
        lens_support_note="놀이 적합성은 상세 정보로 확인합니다.",
        information_needs=(InformationNeedId.ACTIVITY_PLAY,),
        source_facts=bundle,
    )

    assert PresentationFactId.PET_AMENITIES_FACILITIES in {
        item.fact_id for item in result.promoted_items
    }
    assert "activity.play.evidence_unavailable" not in {item.code for item in result.notices}
    assert result.source_evidence[0].source == hit.place.key


def test_missing_shadow_facts_keep_candidate_and_disclose_both_limits() -> None:
    hit = _hit()
    missing = build_candidate_fact_bundle(
        SourceFactKey(source="kto", source_ref="K1"),
        [],
    )

    result = assemble_place_presentation(
        hit,
        _group(hit),
        lens_id="lens:play",
        lens_label="#놀기",
        lens_support_note="넓게 찾은 결과입니다.",
        information_needs=(InformationNeedId.ACTIVITY_PLAY,),
        source_facts=missing,
    )

    assert result.title == "테스트 장소"
    assert {item.code for item in result.notices} == {
        "activity.play.evidence_unavailable",
        "source_facts.missing",
    }


def test_unrequested_missing_detail_fields_do_not_clutter_the_card() -> None:
    hit = _hit()
    bundle = _kto_bundle(
        {"acmpyTypeCd": "전구역 동반가능"},
        detail_state=FactState.KNOWN,
    )

    result = assemble_place_presentation(
        hit,
        _group(hit),
        lens_id="lens:travel",
        lens_label="#나들이",
        lens_support_note="나들이 후보입니다.",
        source_facts=bundle,
    )

    assert not {
        PresentationFactId.PET_AMENITIES_FACILITIES,
        PresentationFactId.PET_PRODUCTS_PROVIDED,
        PresentationFactId.PET_PRODUCTS_PURCHASABLE,
        PresentationFactId.PET_PRODUCTS_RENTABLE,
    } & {
        item.fact_id for item in (*result.core_items, *result.promoted_items, *result.detail_items)
    }


def test_conflicting_source_section_is_not_silently_selected() -> None:
    hit = _hit(source="kcisa", ref="C1")
    key = SourceFactKey(source="kcisa", source_ref="C1")

    def variant(record_ref: str, parking: str) -> SourceFactVariant:
        return SourceFactVariant(
            source_ref="C1",
            record_ref=record_ref,
            occurrence_count=1,
            snapshot="test-snapshot",
            detail_state=DetailAcquisitionState.NOT_APPLICABLE,
            projection=project_kcisa(
                {
                    "카테고리3": "자연관광지",
                    "주차 가능여부": parking,
                }
            ),
        )

    bundle = build_candidate_fact_bundle(
        key,
        [variant("record:yes", "Y"), variant("record:no", "N")],
    )
    result = assemble_place_presentation(
        hit,
        _group(hit),
        lens_id="lens:travel",
        lens_label="#나들이",
        lens_support_note="나들이 후보입니다.",
        information_needs=(InformationNeedId.OPERATIONS_PARKING,),
        source_facts=bundle,
    )

    assert PresentationFactId.OPERATIONS_PARKING not in {
        item.fact_id for item in (*result.core_items, *result.promoted_items, *result.detail_items)
    }
    assert {item.code for item in result.notices} >= {
        "operations.parking.unknown",
        "source_facts.operations.conflict",
    }


def test_borrowed_legacy_field_without_link_receipt_is_not_presented() -> None:
    supporting = PlaceRef(source="kcisa", ref="C2")
    hit = _hit(
        facts=PlaceFacts(homepage="https://borrowed.example", address="서울 마포구"),
        field_sources={
            "facts.homepage": FieldProvenance(source=supporting),
        },
    )

    result = assemble_place_presentation(
        hit,
        _group(hit),
        lens_id="lens:travel",
        lens_label="#나들이",
        lens_support_note="나들이 후보입니다.",
    )

    assert PresentationFactId.CONTACT_HOMEPAGE not in {
        item.fact_id for item in (*result.core_items, *result.promoted_items, *result.detail_items)
    }


def test_bundle_for_another_place_cannot_be_misattributed() -> None:
    hit = _hit()
    wrong = build_candidate_fact_bundle(
        SourceFactKey(source="kto", source_ref="K2"),
        [],
    )

    with pytest.raises(ValueError, match="must belong"):
        assemble_place_presentation(
            hit,
            _group(hit),
            lens_id="lens:travel",
            lens_label="#나들이",
            lens_support_note="나들이 후보입니다.",
            source_facts=wrong,
        )


def test_shadow_facts_do_not_reuse_legacy_hit_evaluation() -> None:
    hit = _hit(
        source="kcisa",
        ref="C1",
        evaluations=PlaceEvaluations(
            dog_access=DogAccessEvaluation(
                state="incompatible",
                reason="dog_disallowed",
            )
        ),
    )

    result = assemble_place_presentation(
        hit,
        _group(hit),
        lens_id="lens:travel",
        lens_label="#나들이",
        lens_support_note="나들이 후보입니다.",
        information_needs=(InformationNeedId.PET_SIZE,),
        source_facts=_kcisa_bundle("모두 가능"),
    )

    shadow_facts = {
        item.fact_id: item
        for item in (*result.core_items, *result.promoted_items, *result.detail_items)
        if item.fact_id in {PresentationFactId.PET_ACCESS_ALLOWED, PresentationFactId.PET_SIZE}
    }
    assert shadow_facts
    assert all(
        item.evaluation_state is EvaluationState.NOT_EVALUATED for item in shadow_facts.values()
    )


def test_size_display_preserves_boundary_and_subject() -> None:
    hit = _hit(source="kcisa", ref="C1")

    result = assemble_place_presentation(
        hit,
        _group(hit),
        lens_id="lens:travel",
        lens_label="#나들이",
        lens_support_note="나들이 후보입니다.",
        information_needs=(InformationNeedId.PET_SIZE,),
        source_facts=_kcisa_bundle("10kg 이하 소형"),
    )

    size = next(
        item
        for item in (*result.core_items, *result.promoted_items, *result.detail_items)
        if item.fact_id is PresentationFactId.PET_SIZE
    )
    assert "10kg 이하" in size.display_text
    assert "중대형견" in size.display_text


def test_unparsed_size_is_unknown_instead_of_open() -> None:
    hit = _hit(source="kcisa", ref="C1")

    result = assemble_place_presentation(
        hit,
        _group(hit),
        lens_id="lens:travel",
        lens_label="#나들이",
        lens_support_note="나들이 후보입니다.",
        information_needs=(InformationNeedId.PET_SIZE,),
        source_facts=_kcisa_bundle("현장 문의"),
    )

    size = next(
        item
        for item in (*result.core_items, *result.promoted_items, *result.detail_items)
        if item.fact_id is PresentationFactId.PET_SIZE
    )
    assert size.source_state is FactState.PARSE_FAILED
    assert size.value is None
    assert size.display_text != "확인된 제한 조건 없음"
    assert "source_facts.projection_issues" in {item.code for item in result.notices}


def test_explicit_open_size_satisfies_size_information_need() -> None:
    hit = _hit(source="kcisa", ref="C1")

    result = assemble_place_presentation(
        hit,
        _group(hit),
        lens_id="lens:travel",
        lens_label="#나들이",
        lens_support_note="나들이 후보입니다.",
        information_needs=(InformationNeedId.PET_SIZE,),
        source_facts=_kcisa_bundle("모두 가능"),
    )

    size = next(
        item
        for item in (*result.core_items, *result.promoted_items, *result.detail_items)
        if item.fact_id is PresentationFactId.PET_SIZE
    )
    assert size.value == "any"
    assert size.display_text == "모든 크기 가능"
    assert "pet.size.unknown" not in {item.code for item in result.notices}
