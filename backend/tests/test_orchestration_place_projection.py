"""Pure projection tests for the consumer-owned Place discovery contract."""

from __future__ import annotations

import json

from daengs_backend.orchestration.adapters._place_contract import _DiscoveryResponse
from daengs_backend.orchestration.adapters.place import (
    MAX_PLACE_CAPABILITY_BYTES,
    project_place_capability_data,
)

from place_capability_cases import discovery_payload


def _project(**kwargs: object) -> dict:
    discovery = _DiscoveryResponse.model_validate(discovery_payload(**kwargs))
    return project_place_capability_data(discovery)


def test_projection_preserves_source_identity_but_drops_internal_material() -> None:
    data = _project()

    candidate = data["groups"][0]["candidates"][0]
    assert candidate["place_id"] == {"source": "kto", "ref": "K1-0"}
    borrowed = next(fact for fact in candidate["facts"] if fact["id"] == "amenities.fact-1")
    assert borrowed["provenance"] == {
        "source": "kcisa",
        "ref": "source-1",
        "role": "supporting",
        "value_origin": "borrowed",
        "link_state": "candidate",
    }
    encoded = json.dumps(data, ensure_ascii=False)
    assert "must-not-cross" not in encoded
    assert "source_evidence" not in encoded
    assert "policy_receipt" not in encoded


def test_fallback_and_unresolved_signal_are_disclosed_without_claiming_a_match() -> None:
    data = _project(
        mapping_scope="product_fallback",
        deferred=True,
        issue="unsupported_semantic_intent",
    )

    assert "직접 확인할 근거가 부족" in data["answer"]
    notices = data["notices"]
    assert {notice["code"] for notice in notices} >= {
        "unsupported_semantic_intent",
        "place.signal_deferred",
    }
    assert "조용한 장소" not in data["answer"]


def test_explicit_open_discovery_is_not_mislabeled_as_an_unsupported_fallback() -> None:
    data = _project(mapping_scope="open_discovery")

    assert "직접 확인할 근거가 부족" not in data["answer"]
    assert "가까운 장소 1곳" in data["answer"]


def test_needs_selection_survives_as_an_actionable_refinement() -> None:
    data = _project(refinement=True)

    [refinement] = data["refinements"]
    assert refinement["lens_id"] == "signal:price"
    assert refinement["options"][0]["id"] == "cost.travel_distance"


def test_projection_has_an_independent_byte_budget() -> None:
    data = _project(group_sizes=(5, 5, 5), large=True)

    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(encoded) <= MAX_PLACE_CAPABILITY_BYTES
    assert sum(len(group["candidates"]) for group in data["groups"]) <= 9
    assert "place.capability_projection_applied" in {
        notice["code"] for notice in data["notices"]
    }

