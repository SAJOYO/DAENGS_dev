"""Frozen pre-extraction JSON, schema and SHA-256 contracts from dev 390ddd0c."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from daengs_backend.schemas.walk_photo import PhotoManifestWrite
from daengs_walk.diary.contracts.input import Note
from daengs_walk.value_contracts import Point, digest
from daengs_walk.weather import GridTemperature

GOLDEN = json.loads(
    (Path(__file__).parents[1] / "fixtures/provider-contracts-v1.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "name,model", [("photo", PhotoManifestWrite), ("weather", GridTemperature), ("note", Note)]
)
def test_original_wire_schema_and_hash(name, model):
    expected = GOLDEN["models"][name]
    value = model.model_validate(expected["input"])
    assert value.model_dump_json() == expected["json"]
    assert model.model_json_schema() == expected["schema"]
    assert digest(value) == expected["sha256"]
    assert digest(value.model_dump(mode="json")) == expected["sha256"]


def test_original_json_hashes():
    for case in GOLDEN["json_values"]:
        assert digest(case["input"]) == case["sha256"]


def test_shared_types_preserve_diary_compatibility_and_validation():
    from daengs_walk.diary.contracts.input import Point as DiaryPoint
    from daengs_walk.diary.slots.temperature import GridTemperature as DiaryTemperature

    assert DiaryPoint is Point
    assert DiaryTemperature is GridTemperature
    with pytest.raises(ValidationError):
        Point(lat=float("nan"), lng=127)
    with pytest.raises(ValueError):
        digest({"temperature": float("inf")})


@pytest.mark.parametrize(
    "changes",
    [
        {"observed_at": "2026-09-14T12:01:00+09:00"},
        {"issued_at": "2026-09-14T11:00:00+09:00"},
        {"requested_at": "2026-09-14T11:59:00+09:00"},
        {"fetched_at": "2026-09-14T12:05:00+09:00"},
        {"requested_at": "2026-09-14T12:10:00"},
    ],
)
def test_observation_contract_rejects_invalid_time_provenance(changes):
    with pytest.raises(ValidationError):
        GridTemperature.model_validate({**GOLDEN["models"]["weather"]["input"], **changes})
