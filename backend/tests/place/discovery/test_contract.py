import pytest
from pydantic import ValidationError

from daengs_place.place.discovery.contract import (
    MAX_CANDIDATES_PER_LENS,
    MAX_DISCOVERY_CANDIDATES,
    MAX_DISCOVERY_LENSES,
    MAX_DISCOVERY_SERIALIZED_BYTES,
    PlaceDiscoveryRequest,
    PlaceDiscoveryResultPolicy,
)
from daengs_place.place.planning.contract import (
    PlaceSearchConditions,
    PlaceSpatialConstraint,
)

_SPATIAL = PlaceSpatialConstraint(lat=37.5563, lng=126.9236, radius_m=3_000)


def test_request_preserves_query_and_accepts_values_not_dog_identity() -> None:
    conditions = PlaceSearchConditions(
        dog_size="small",
        dog_weight_kg=4.2,
        dog_age_years=3,
    )
    request = PlaceDiscoveryRequest(
        query="  강아지와 갈 카페  ",
        spatial=_SPATIAL,
        conditions=conditions,
    )

    assert request.query == "  강아지와 갈 카페  "
    assert request.conditions == conditions
    with pytest.raises(ValidationError, match="query must not be blank"):
        PlaceDiscoveryRequest(query="   ", spatial=_SPATIAL)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlaceDiscoveryRequest.model_validate(
            {
                "query": "강아지와 갈 카페",
                "spatial": _SPATIAL.model_dump(),
                "dog_id": "dog-123",
            }
        )


def test_request_does_not_restore_the_old_client_result_limit() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlaceDiscoveryRequest.model_validate(
            {
                "query": "카페",
                "spatial": _SPATIAL.model_dump(),
                "limit_per_kind": 3_000,
            }
        )


def test_result_policy_has_hard_server_caps() -> None:
    with pytest.raises(ValidationError):
        PlaceDiscoveryResultPolicy(max_executable_lenses=MAX_DISCOVERY_LENSES + 1)
    with pytest.raises(ValidationError):
        PlaceDiscoveryResultPolicy(max_candidates_per_lens=MAX_CANDIDATES_PER_LENS + 1)
    with pytest.raises(ValidationError):
        PlaceDiscoveryResultPolicy(max_total_candidates=MAX_DISCOVERY_CANDIDATES + 1)
    with pytest.raises(ValidationError):
        PlaceDiscoveryResultPolicy(max_serialized_bytes=MAX_DISCOVERY_SERIALIZED_BYTES + 1)
    with pytest.raises(ValidationError, match="cover every executable lens"):
        PlaceDiscoveryResultPolicy(
            max_executable_lenses=3,
            max_total_candidates=2,
        )
