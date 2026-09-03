import json
from pathlib import Path

from daengs_place.place.source_facts.kto import project_kto
from daengs_place.place.source_facts.states import FactState

FIXTURE = Path(__file__).parent / "fixtures" / "kto.json"


def _cases() -> dict[str, dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {item["id"]: item for item in payload["cases"]}


def _project(case: dict):
    return project_kto(
        case["listing"],
        case["detail"],
        detail_state=FactState(case["detail_state"]),
    )


def test_kto_taxonomy_codes_are_preserved_without_invented_labels():
    projection = _project(_cases()["full-mountain"])

    assert projection.purpose.primary == "travel"
    assert [node.code for node in projection.purpose.taxonomy_path] == [
        "NA",
        "NA01",
        "NA010100",
    ]
    assert all(node.label is None for node in projection.purpose.taxonomy_path)


def test_kto_detail_fields_become_common_predicates():
    projection = _project(_cases()["full-mountain"])
    predicates = {(item.code, item.applies_to) for item in projection.restrictions.predicates}

    assert projection.pet_access.scope == "full"
    assert projection.pet_access.companion_text == "전 견종 동반 가능"
    assert projection.evidence["pet_access.companion_text"].state is FactState.KNOWN
    assert ("require:leash", "all") in predicates
    assert ("require:muzzle", "breed:guard") in predicates
    assert ("require:poop_bag", "all") in predicates
    assert projection.restrictions.parse_state == "partial"  # 자유문장은 원문을 유지한다


def test_partial_scope_weight_requirements_and_amenity_stay_separate():
    projection = _project(_cases()["partial-weight-amenity"])
    codes = {item.code for item in projection.restrictions.predicates}
    weight = next(
        item
        for item in projection.restrictions.predicates
        if item.code == "deny:size" and "max_kg" in item.params
    )

    assert projection.pet_access.scope == "partial"
    assert {"require:leash", "require:stroller", "require:carrier"} <= codes
    assert weight.params == {"max_kg": "20", "inclusive": "false"}
    assert projection.amenities.facilities == (
        "반려견놀이터 / 반려견 동반 가능 객실 / 펫 프렌들리 카페",
    )


def test_not_fetched_detail_is_not_reported_as_no_restrictions():
    projection = _project(_cases()["not-fetched"])

    assert projection.pet_access.scope is None
    assert projection.restrictions.state == "unknown"
    assert projection.evidence["pet_access.scope"].state is FactState.NOT_FETCHED
    assert projection.evidence["amenities.facilities"].state is FactState.NOT_FETCHED
    assert projection.evidence["pet_access.companion_text"].state is FactState.NOT_FETCHED


def test_fetched_detail_with_absent_field_is_not_provided():
    projection = project_kto(
        {"contenttypeid": "12"},
        {"acmpyTypeCd": "전구역 동반가능"},
        detail_state=FactState.KNOWN,
    )

    assert projection.evidence["amenities.facilities"].state is FactState.NOT_PROVIDED
    assert projection.evidence["restrictions.predicates"].state is FactState.NOT_PROVIDED


def test_explicit_no_restrictions_is_not_downgraded_to_unknown():
    projection = project_kto(
        {"contenttypeid": "12"},
        {
            "acmpyTypeCd": "전구역 동반가능",
            "acmpyNeedMtr": "없음",
            "acmpyPsblCpam": "전 견종 동반 가능",
            "etcAcmpyInfo": "없음",
            "relaAcdntRiskMtr": "해당없음",
        },
        detail_state=FactState.KNOWN,
    )

    assert projection.restrictions.state == "none_confirmed"
    assert projection.restrictions.predicates == ()
    assert projection.evidence["restrictions.predicates"].state is FactState.KNOWN


def test_unread_free_text_is_raw_only_not_silently_empty():
    projection = _project(_cases()["unparseable-detail"])

    assert projection.restrictions.state == "restricted"
    assert projection.restrictions.parse_state == "raw_only"
    assert "etcAcmpyInfo" in projection.restrictions.raw
    assert "incomplete_restriction_parse" in {issue.code for issue in projection.issues}


def test_invalid_taxonomy_hierarchy_is_visible():
    projection = _project(_cases()["invalid-taxonomy"])

    assert "invalid_taxonomy_path" in {issue.code for issue in projection.issues}
