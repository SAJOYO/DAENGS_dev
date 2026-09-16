"""Diary admission policy for a supplied historical temperature observation."""

from daengs_walk.weather import GridTemperature


def temperature_candidate(saved, anchor, scene_scope, policy, reject):
    from daengs_walk.diary.contracts.input import digest
    from daengs_walk.diary.slots.sources import evidence

    try:
        value = GridTemperature.model_validate(saved.payload)
    except ValueError:
        reject("environment", saved.id, "invalid_grid_temperature", "unknown")
        return None
    if (
        value.query_point != anchor.point
        or saved.query_point != anchor.point
        or value.requested_at != anchor.event_at
        or value.fetched_at != saved.retrieved_at
        or saved.temporal_basis != "source_observation"
    ):
        reject("environment", saved.id, "weather_query_mismatch", "unknown")
        return None
    age = (anchor.event_at - value.observed_at).total_seconds()
    details = {
        "actual": age,
        "limit": policy.weather_max_age_s,
        "unit": "seconds",
        "observed_at": value.observed_at.isoformat(),
        "scene_at": anchor.event_at.isoformat(),
        "grid": list(value.grid),
    }
    if age > policy.weather_max_age_s:
        reject("environment", saved.id, "weather_observation_too_old", **details)
        return None
    return evidence(
        "environment",
        "grid_temperature_observation",
        saved.id,
        digest(saved),
        {"provider": value.provider, "grid": value.grid},
        {
            **value.model_dump(mode="json", exclude={"query_point", "requested_at", "fetched_at"}),
            "retrieved_at": value.fetched_at.isoformat(),
            "observation_age_s": age,
            "interpretation": "기록 시각 이하의 해당 격자 기온 관측. 현장 체감이나 기록 순간의 직접 측정은 아님.",
        },
        (-value.observed_at.timestamp(), -value.fetched_at.timestamp()),
        scope={**scene_scope, "grid": value.grid, "observed_at": value.observed_at.isoformat()},
        diagnostics=details,
    )
