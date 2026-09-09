"""vet_contact 의 payload 계약 — 좌표는 둘 다 있거나 둘 다 없거나."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    VetContactPayload,
)


def test_coordinates_are_optional_together() -> None:
    payload = VetContactPayload(at_night=True)
    assert payload.lat is None
    assert payload.lon is None


def test_half_a_coordinate_is_rejected() -> None:
    with pytest.raises(ValidationError, match="lat and lon must be given together"):
        VetContactPayload(lat=37.5665, at_night=False)


def test_out_of_box_coordinate_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VetContactPayload(lat=10.0, lon=126.978, at_night=False)


def test_request_parses_the_payload_by_capability_name() -> None:
    request = CapabilityRequest.model_validate(
        {
            "capability": "vet_contact",
            "payload": {"lat": 37.5665, "lon": 126.978, "at_night": False},
        }
    )
    assert request.capability is CapabilityName.VET_CONTACT
    assert isinstance(request.payload, VetContactPayload)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VetContactPayload(at_night=False, rating=5)
