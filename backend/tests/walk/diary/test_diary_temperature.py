"""A real grid observation reaches prose only for the queried scene and permitted age."""

from datetime import timedelta

import pytest

from daengs_backend.services.walk_diary.legacy.slots import write_slot_preview
from daengs_evals.diary_slots_demo import demo_input
from daengs_walk.diary_input import DiaryInput, digest
from daengs_walk.diary_temperature import GridTemperature
from tests.walk.diary.test_diary_slots import prepare


def input_with_temperature():
    source, route, _ = demo_input()
    raw = source.model_dump(mode="json")
    record = source.records[0]
    at = record.anchor.event_at.replace(minute=0, second=0, microsecond=0)
    snapshot = GridTemperature(
        provider="kma-vilage-fcst:ncst",
        query_point=record.anchor.point,
        requested_at=record.anchor.event_at,
        fetched_at=source.ended_at,
        grid=(61, 125),
        observed_at=at,
        issued_at=at,
        temperature_c=22.5,
    ).model_dump(mode="json")
    saved = next(b for b in raw["backgrounds"] if b["id"] == "environment-1")
    saved.update(
        payload=snapshot,
        payload_sha256=digest(snapshot),
        temporal_basis="source_observation",
        valid_from=None,
        valid_until=None,
        retrieved_at=source.ended_at.isoformat(),
    )
    return raw, route, saved


async def test_grid_temperature_joins_space_and_motion_without_rewriting_original():
    raw, route, _ = input_with_temperature()
    preview = prepare(DiaryInput.model_validate(raw), route)
    stamp = preview.stamps[1]
    assert {e.part for e in stamp.evidence} == {"space", "environment", "motion"}
    e = next(e for e in stamp.evidence if e.part == "environment")
    assert e.role == "grid_temperature_observation" and e.facts["temperature_c"] == 22.5
    assert "valid_until" not in e.facts and "area_radius_m" not in e.facts

    async def generate(payload, schema):
        return {
            "scenes": [
                {
                    "scene_id": s["scene_id"],
                    "text": "앞선 지역 관측에서 기온은 22.5도였다."
                    if s["scene_id"] == stamp.scene_id
                    else "",
                    "evidence_ids": [e.id] if s["scene_id"] == stamp.scene_id else [],
                    "action_id": None,
                }
                for s in payload["scenes"]
            ]
        }

    written = await write_slot_preview(preview, generate)
    assert written.model_status == "accepted"
    assert written.scenes[1].body.endswith(preview.scenes[1].body)
    assert written.citations[stamp.scene_id] == (e.id,)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("old", "weather_observation_too_old"),
        ("future", "invalid_grid_temperature"),
        ("point", "weather_query_mismatch"),
        ("request", "weather_query_mismatch"),
        ("fetched", "weather_query_mismatch"),
        ("unit", "invalid_grid_temperature"),
        ("forecast", "invalid_grid_temperature"),
    ],
)
def test_weather_binding_and_age_exclusions(change, reason):
    raw, route, saved = input_with_temperature()
    value = saved["payload"]
    at = DiaryInput.model_validate(raw).records[0].anchor.event_at
    if change == "old":
        value["observed_at"] = value["issued_at"] = (
            at.replace(minute=0, second=0) - timedelta(hours=3)
        ).isoformat()
    elif change == "future":
        value["observed_at"] = value["issued_at"] = (
            at.replace(minute=0, second=0) + timedelta(hours=1)
        ).isoformat()
    elif change == "point":
        value["query_point"]["lat"] = 38
    elif change == "request":
        value["requested_at"] = (at + timedelta(seconds=1)).isoformat()
    elif change == "fetched":
        value["fetched_at"] = (at + timedelta(hours=1)).isoformat()
    elif change == "unit":
        value["unit"] = "fahrenheit"
    elif change == "forecast":
        value["provider"] = "kma-vilage-fcst:fcst"
    saved["payload_sha256"] = digest(value)
    stamp = prepare(DiaryInput.model_validate(raw), route).stamps[1]
    assert not any(e.part == "environment" for e in stamp.evidence)
    assert any(d.reason == reason for d in stamp.decisions)


def test_weather_age_policy_reports_actual_and_limit_and_zero_capacity_is_independent():
    raw, route, _ = input_with_temperature()
    source = DiaryInput.model_validate(raw)
    stamp = prepare(source, route, weather_max_age_s=60).stamps[1]
    decision = next(d for d in stamp.decisions if d.reason == "weather_observation_too_old")
    assert decision.details["actual"] == 80 and decision.details["limit"] == 60
    stamp = prepare(source, route, environment_slots=0).stamps[1]
    assert any(d.part == "environment" and d.admission == "part_capacity" for d in stamp.decisions)
