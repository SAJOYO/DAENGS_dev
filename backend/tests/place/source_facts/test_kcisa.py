import json
from pathlib import Path

from daengs_place.place.source_facts.kcisa import project_kcisa
from daengs_place.place.source_facts.states import FactState

FIXTURE = Path(__file__).parent / "fixtures" / "kcisa.json"


def _cases() -> dict[str, dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {item["id"]: item["row"] for item in payload["cases"]}


def test_purpose_and_zone_hints_preserve_source_meaning():
    projection = project_kcisa(_cases()["outdoor-only-museum"])

    assert projection.purpose.primary == "museum"
    assert [node.label for node in projection.purpose.taxonomy_path] == [
        "반려동물업",
        "반려동반여행",
        "박물관",
    ]
    assert projection.pet_access.source_indoor is False
    assert projection.pet_access.source_outdoor is True
    assert projection.pet_access.zone_hints == ("outdoor",)
    assert "zone:outdoor_only" in {item.code for item in projection.restrictions.predicates}


def test_explicit_zone_sentence_wins_by_exposing_a_conflict():
    projection = project_kcisa(_cases()["zone-conflict"])

    assert projection.pet_access.zone_hints == ("indoor", "outdoor")
    assert "zone:outdoor_only" in {item.code for item in projection.restrictions.predicates}
    assert "zone_flag_conflict" in {issue.code for issue in projection.issues}
    assert projection.state.value == "partial"


def test_denied_record_is_projectable_even_though_current_ingest_excludes_it():
    projection = project_kcisa(_cases()["explicit-denied"])

    assert projection.pet_access.allowed is False
    assert projection.pet_access.zone_hints == ()
    assert projection.restrictions.state == "not_applicable"
    assert projection.evidence["pet_access.allowed"].state.value == "known"


def test_size_condition_and_fee_are_separate_facts():
    projection = project_kcisa(_cases()["size-and-fee"])
    predicates = {item.code: item for item in projection.restrictions.predicates}

    assert predicates["deny:size"].applies_to == "size:medium_up"
    assert predicates["deny:size"].params == {
        "max_kg": "10.0",
        "inclusive": "true",
    }
    assert predicates["require:vaccination"].applies_to == "all"
    assert projection.pet_access.exclusive is True
    assert projection.pet_fee.amount_krw == 10_000


def test_size_boundary_keeps_under_distinct_from_at_most():
    base = _cases()["size-and-fee"]
    under = project_kcisa({**base, "입장 가능 동물 크기": "10kg 미만 소형"})
    at_most = project_kcisa({**base, "입장 가능 동물 크기": "10kg 이하 소형"})

    under_size = next(item for item in under.restrictions.predicates if item.code == "deny:size")
    at_most_size = next(
        item for item in at_most.restrictions.predicates if item.code == "deny:size"
    )
    assert under_size.params == {"max_kg": "10.0", "inclusive": "false"}
    assert at_most_size.params == {"max_kg": "10.0", "inclusive": "true"}


def test_only_a_single_exact_fee_becomes_amount_krw():
    base = _cases()["size-and-fee"]

    fee_range = project_kcisa({**base, "애견 동반 추가 요금": "2,000~3,000원"})
    fee_tiers = project_kcisa(
        {
            **base,
            "애견 동반 추가 요금": ("8kg 미만 4,000원, 15kg 미만 7,000원, 15kg 이상 10,000원"),
        }
    )
    not_applicable = project_kcisa({**base, "애견 동반 추가 요금": "해당없음"})

    assert fee_range.pet_fee.amount_krw is None
    assert fee_tiers.pet_fee.amount_krw is None
    assert fee_range.evidence["pet_fee.amount_krw"].state.value == "parse_failed"
    assert not_applicable.pet_fee.amount_krw is None
    assert not_applicable.evidence["pet_fee.amount_krw"].state.value == "not_applicable"


def test_species_denial_is_not_lost_inside_size_text():
    projection = project_kcisa(_cases()["dog-denied-by-species"])
    codes = [item.code for item in projection.restrictions.predicates]

    assert codes.count("deny:species_dog") == 1


def test_unparsed_size_text_is_not_reported_as_a_known_open_boundary():
    projection = project_kcisa(
        {
            **_cases()["size-and-fee"],
            "입장 가능 동물 크기": "현장 문의",
        }
    )

    assert projection.evidence["restrictions.size"].state is FactState.PARSE_FAILED
    assert "unparsed_size_constraint" in {item.code for item in projection.issues}
