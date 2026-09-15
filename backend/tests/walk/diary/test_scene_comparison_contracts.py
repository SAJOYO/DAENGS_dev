"""Boundary checks for the upcoming writer, with no API/model calls."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from daengs_walk.diary.relational.contracts import ActionInput, SpaceInput
from daengs_walk.diary.relational.scene_comparison_contracts import (
    SpaceComparisonAnswer,
    SpaceComparisonInput,
)


def request_data():
    def scene(n):
        return {
            "scene_id": f"s{n}",
            "walk_id": "walk",
            "recorded_at": f"2026-09-15T09:{n}0:00+09:00",
            "point": {"lat": 37.4 + n / 100, "lng": 127},
            "position_basis": "observed",
            "collection": {"surrounding_object": "partial"},
            "facts": [
                {
                    "id": f"s{n}:park",
                    "family": "surrounding_object",
                    "value": {"name": "공원", "distance_m": 200 - n * 50},
                    "scope": {"kind": "registered_point", "description": "등록점 거리"},
                    "subject_key": "parks:123",
                    "source_refs": ["raw:123"],
                    "time_meaning": "지도 기준일 미상; 기록 시각의 관측이 아님",
                }
            ],
        }

    empty = {"status": "not_applicable", "reason": "비교 재료 없음", "items": []}
    return {
        "earlier": scene(1),
        "current": scene(2),
        "connection": {
            "earlier_scene_id": "s1",
            "current_scene_id": "s2",
            "elapsed_seconds": 600,
            "route_status": "unavailable",
            "scope": "두 기록점만 확인",
        },
        "relation_slots": {
            "background": deepcopy(empty),
            "area_context": deepcopy(empty),
            "proximity": {
                "status": "confirmed",
                "reason": "같은 등록점 대응",
                "items": [
                    {
                        "id": "r:park",
                        "family": "surrounding_object",
                        "axis": "object_distance",
                        "result": "same_object",
                        "earlier_evidence_ids": ["s1:park"],
                        "current_evidence_ids": ["s2:park"],
                        "distance_delta_m": -50,
                        "scope": "양 끝 거리 차이; 중간 접근 과정 미확인",
                    }
                ],
            },
        },
    }


def test_round_trip_keeps_unknown_source_time_and_endpoint_scope():
    request = SpaceComparisonInput.model_validate(request_data())
    restored = SpaceComparisonInput.model_validate_json(request.model_dump_json())
    assert restored == request
    assert restored.current.facts[0].observed_at is None
    assert restored.connection.route_evidence_ids == ()
    assert restored.citation_ids == ("s1:park", "s2:park")
    answer = SpaceComparisonAnswer(
        focus="같은 공원과의 거리 차이",
        text="공원과 가까워졌다.",
        relation_ids=("r:park",),
        evidence_ids=("s1:park", "s2:park"),
    )
    answer.validate_against(restored)
    # Audit source identity is not a writer citation, even when present in the input.
    with pytest.raises(ValueError, match="unknown cited evidence"):
        answer.model_copy(update={"evidence_ids": ("raw:123",)}).validate_against(restored)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_endpoint",
        "different_object",
        "time_as_location",
        "wrong_elapsed",
        "unbacked_route",
        "weather",
        "action",
    ],
)
def test_invalid_comparisons_are_rejected(mutation):
    data = request_data()
    relation = data["relation_slots"]["proximity"]["items"][0]
    if mutation == "wrong_endpoint":
        relation["earlier_evidence_ids"] = ["s2:park"]
    elif mutation == "different_object":
        data["current"]["facts"][0]["subject_key"] = "parks:456"
    elif mutation == "time_as_location":
        relation["axis"] = "observation_time"
    elif mutation == "wrong_elapsed":
        data["connection"]["elapsed_seconds"] = 1
    elif mutation == "unbacked_route":
        data["connection"]["route_status"] = "connected"
    elif mutation == "weather":
        data["current"]["weather"] = {"temperature": 24.2}
    else:
        data["current"]["recorded_action"] = {"action": "냄새 맡기"}
    with pytest.raises(ValidationError):
        SpaceComparisonInput.model_validate(data)


def test_one_sided_lookup_is_not_disappearance_or_distance_change():
    data = request_data()
    data["current"]["facts"] = []
    data["current"]["collection"]["surrounding_object"] = "failed"
    relation = data["relation_slots"]["proximity"]["items"][0]
    relation.update(
        result="only_one_snapshot_has_evidence", current_evidence_ids=[], distance_delta_m=None
    )
    data["relation_slots"]["proximity"]["status"] = "insufficient_evidence"
    SpaceComparisonInput.model_validate(data)
    relation["distance_delta_m"] = 50
    with pytest.raises(ValidationError):
        SpaceComparisonInput.model_validate(data)


def test_initial_scene_and_legacy_contracts_remain_separate():
    data = request_data()
    data.update(earlier=None, connection=None)
    data["relation_slots"]["proximity"] = {
        "status": "not_applicable",
        "reason": "첫 기록",
        "items": [],
    }
    SpaceComparisonInput.model_validate(data)
    SpaceInput(mode="current_context", current_space=(), relations=(), required_relation_ids=())
    with pytest.raises(ValidationError):
        ActionInput.model_validate(data)
