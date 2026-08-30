"""제약 술어가 계약까지 흐르고, 사실 상태가 구분되는가.

Decision: #70

PR 1 은 표를, PR 2 는 저장을 고정했다. 여기서 지키는 것은 **노출**이다.

  라벨 소유   서버가 붙인다. 웹과 Android 가 같은 문구를 봐야 한다 (결정 #65 §6)
  한정어      `applies_to` 가 `all` 이 아니면 라벨에 남는다 — 안 남으면 소형견
              보호자가 "입마개 필요" 를 본다
  원문        `partial`·`raw_only` 에만 싣는다. 칩만 보이면 완결로 읽힌다
  커버리지    칩 0개인 세 이유를 따로 센다 — 미상과 "제한 없음" 이 섞이면 안 된다
"""

import pytest

from app.place.adapters import _restriction_facts
from app.place.contracts import (
    FieldProvenance,
    PlaceClassification,
    PlaceFacts,
    PlaceMatch,
    PlaceRef,
    PlaceResult,
    RestrictionFacts,
)
from app.place.facility_resolver import RestrictionPredicateOut, RestrictionsOut
from app.place.search import PlaceSearchConditions, _hit, _restriction_coverage

KEY = PlaceRef(source="kcisa", ref="X1")


def _place(restrictions: RestrictionFacts | None) -> PlaceResult:
    return PlaceResult(
        key=KEY,
        name="테스트",
        lat=37.5,
        lng=127.0,
        distance_m=10,
        match=PlaceMatch(source=KEY, kind="cafe"),
        classifications=[PlaceClassification(
            source=KEY, source_category="카페", kind="cafe", mapping_version="v",
        )],
        facts=PlaceFacts(restrictions=restrictions),
    )


def test_server_owns_the_label():
    facts = _restriction_facts(RestrictionsOut(
        state="restricted",
        parse_state="mapped",
        predicates=[RestrictionPredicateOut(code="require:leash")],
    ))
    assert facts.chips[0].label == "목줄"
    assert facts.chips[0].code == "require:leash"


def test_conditional_chip_keeps_its_qualifier():
    """한정어가 사라지면 2kg 개 보호자가 없는 제한을 본다."""
    facts = _restriction_facts(RestrictionsOut(
        state="restricted",
        parse_state="mapped",
        predicates=[
            RestrictionPredicateOut(code="require:muzzle", applies_to="size:large"),
            RestrictionPredicateOut(code="require:leash"),
        ],
    ))
    assert [chip.label for chip in facts.chips] == ["입마개·대형견", "목줄"]
    assert facts.chips[0].applies_to == "size:large"
    assert facts.chips[1].applies_to == "all"


def test_unlabeled_code_is_rejected_not_dropped():
    """칩을 조용히 빼면 사용자는 조건이 없는 줄 알고 우리는 그 사실을 영영 모른다."""
    with pytest.raises(ValueError, match="no label"):
        _restriction_facts(RestrictionsOut(
            state="restricted",
            parse_state="mapped",
            predicates=[RestrictionPredicateOut(code="require:teleport")],
        ))


def test_raw_rides_along_only_when_predicates_fall_short():
    partial = _restriction_facts(RestrictionsOut(
        state="restricted", parse_state="partial",
        predicates=[RestrictionPredicateOut(code="require:leash")], raw="원문",
    ))
    mapped = _restriction_facts(RestrictionsOut(
        state="restricted", parse_state="mapped",
        predicates=[RestrictionPredicateOut(code="require:leash")],
    ))
    assert partial.raw == "원문"
    assert mapped.raw is None


def test_coverage_keeps_unknown_and_none_apart():
    """이게 합쳐지면 "정보 없는 층" 이 "조건 없는 동네" 로 읽힌다."""
    coverage = _restriction_coverage([
        _place(RestrictionFacts(state="none_confirmed", parse_state="mapped")),
        _place(RestrictionFacts(state="none_confirmed", parse_state="mapped")),
        _place(RestrictionFacts(state="not_applicable", parse_state="mapped")),
        _place(RestrictionFacts(state="unknown")),
        _place(RestrictionFacts(state="restricted", parse_state="partial")),
        _place(RestrictionFacts(state="restricted", parse_state="mapped")),
    ])
    assert coverage.none_confirmed == 2
    assert coverage.not_applicable == 1
    assert coverage.unknown == 1
    assert coverage.restricted == 2
    assert coverage.needs_raw == 1


def test_missing_restrictions_counts_as_unknown_not_none():
    """아직 파생 안 된 행도 "제한 없음" 이 아니다."""
    coverage = _restriction_coverage([_place(None)])
    assert coverage.unknown == 1
    assert coverage.none_confirmed == 0
    assert coverage.not_applicable == 0


def test_unverified_borrowed_restrictions_are_shown_but_never_decide_the_verdict():
    """미검증 링크는 다른 장소의 제한을 보여줄 수는 있어도 이 장소의 불가를 증명하지 못한다."""
    place = _place(RestrictionFacts(
        state="restricted",
        parse_state="mapped",
        chips=[{
            "code": "deny:size", "label": "대형견 불가", "applies_to": "size:large",
        }],
    ))
    place.field_sources["facts.restrictions"] = FieldProvenance(
        source=PlaceRef(source="kcisa", ref="borrowed:1"),
        as_of="2025-03-24",
    )

    hit = _hit(place, PlaceSearchConditions(dog_size="large", dog_weight_kg=34.0))
    evaluation = hit.evaluations.restrictions

    assert evaluation is not None
    assert (evaluation.state, evaluation.reason) == ("unknown", "unverified_source_match")
    assert [chip.code for chip in evaluation.chips] == ["deny:size"]
    assert evaluation.blocking == []
    assert [chip.code for chip in hit.place.facts.restrictions.chips] == ["deny:size"]
