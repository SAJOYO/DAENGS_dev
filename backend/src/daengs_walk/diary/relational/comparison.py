"""Keep record-location context distinct from physical-world time change."""

from datetime import datetime
from math import asin, cos, isfinite, radians, sin, sqrt

from pydantic import Field, ValidationError

from daengs_walk.diary.relational.contracts import RecordChronology
from daengs_walk.value_contracts import ValueContract as DiaryContract


class ObservationBasis(DiaryContract):
    source_series: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    observed_at: datetime
    support_radius_m: float = Field(ge=0, allow_inf_nan=False)


def aware_time(value):
    """Never silently treat naive timestamps as UTC or local time."""
    try:
        at = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return at if at.tzinfo is not None and at.utcoffset() is not None else None


def separation(previous, current):
    try:
        points = [f["anchor"]["point"] for f in (previous, current)]
        lat1, lon1, lat2, lon2 = [float(v) for p in points for v in (p["lat"], p["lng"])]
        if not all(isfinite(v) for v in (lat1, lon1, lat2, lon2)) or not (
            abs(lat1) <= 90 and abs(lat2) <= 90 and abs(lon1) <= 180 and abs(lon2) <= 180
        ):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    h = (
        sin(radians(lat2 - lat1) / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lon2 - lon1) / 2) ** 2
    )
    return 6371000 * 2 * asin(sqrt(max(0.0, min(1.0, h))))


def compare_record_context(previous, current, role):
    """Two located records may differ without any new source observation.

    This compares *attached normalized context*, not contemporaneous physical
    conditions, visits, world change, or any intermediate route coverage.
    """
    if role not in {"point_land_cover", "location_label", "road_address"}:
        return None, {"reason": "unsupported_context_role"}
    session = previous.get("walk_session")
    if not session or session != current.get("walk_session"):
        return None, {"reason": "same_walk_not_established"}
    times = [aware_time(f.get("anchor", {}).get("event_at")) for f in (previous, current)]
    if not all(times) or times[0] >= times[1]:
        return None, {"reason": "record_chronology_not_established"}
    radii = []
    for f in (previous, current):
        anchor = f["anchor"]
        radius = anchor.get("accuracy_m")
        if (
            anchor.get("method") != "observed"
            or anchor.get("position_state") != "resolved"
            or isinstance(radius, bool)
            or not isinstance(radius, (int, float))
            or not isfinite(radius)
            or radius < 0
        ):
            return None, {"reason": "uncertain_record_location"}
        radii.append(radius)
    distance = separation(previous, current)
    if distance is None or distance <= sum(radii):
        return None, {"reason": "overlapping_record_locations"}
    return "record_location", {
        "same_walk": True,
        "separation_m": distance,
        "comparison_subject": "normalized_context_attached_to_record_locations",
        "observation_time_relationship": "unknown",
        "physical_world_change": "not_asserted",
        "location_uncertainty_m": radii,
    }


def compare_basis(previous, current, role):
    raw = [f.get("observation_basis", {}).get(role) for f in (previous, current)]
    if not all(raw):
        axis, basis = compare_record_context(previous, current, role)
        return (axis, basis) if axis else (None, {"reason": "missing_observation_basis", **basis})
    try:
        a, b = (ObservationBasis.model_validate(x) for x in raw)
    except ValidationError:
        return None, {"reason": "invalid_observation_basis"}
    if a.source_series != b.source_series:
        return None, {"reason": "incomparable_source_series"}
    sessions = [f.get("walk_session") for f in (previous, current)]
    basis = {
        "left": a.model_dump(mode="json"),
        "right": b.model_dump(mode="json"),
        "same_walk": sessions[0] == sessions[1] if all(sessions) else None,
    }
    if a.subject_key == b.subject_key:
        if a.observed_at.tzinfo and b.observed_at.tzinfo and a.observed_at < b.observed_at:
            return "observation_time", basis
        return None, {**basis, "reason": "same_subject_without_new_observation"}
    distance = separation(previous, current)
    if (
        distance is not None
        and distance > a.support_radius_m + b.support_radius_m
        and a.observed_at.tzinfo
        and b.observed_at.tzinfo
        and a.observed_at == b.observed_at
    ):
        return "location", {**basis, "separation_m": distance}
    return None, {**basis, "reason": "overlapping_support_or_different_epochs"}


def writing_relation(relation):
    axis = relation["comparison_axis"]
    subjects = []
    for label, endpoint, side in (
        ("앞 기록 위치", "earlier_record_point", "left"),
        ("현재 기록 위치", "current_record_point", "right"),
    ):
        observation = relation[endpoint]["observation"]
        basis = relation["comparison_basis"].get(side, {})
        subjects.append(
            {
                "subject": "동일한 공간 대상" if axis == "observation_time" else label,
                "observed_at": basis.get("observed_at"),
                "value": observation["material"],
                "evidence_scope": observation["relation"],
                "source_time": observation.get("time_meaning"),
            }
        )
    earlier = relation["earlier_record_point"]["recorded_at"]
    current = relation["current_record_point"]["recorded_at"]
    left, right = aware_time(earlier), aware_time(current)
    elapsed = (right - left).total_seconds() if left and right and right >= left else None
    observed = [aware_time(s["observed_at"]) for s in subjects]
    chronology = RecordChronology(
        same_walk=relation["comparison_basis"].get("same_walk"),
        earlier_recorded_at=earlier,
        current_recorded_at=current,
        elapsed_record_seconds=elapsed,
        location_relationship={
            "location": "distinct_locations",
            "record_location": "distinct_record_locations",
            "observation_time": "same_source_subject",
        }[axis],
        same_source_observation_time=observed[0] == observed[1] if all(observed) else None,
    )
    return {
        "id": relation["id"],
        "comparison_axis": axis,
        "attribute": relation["role"],
        "subjects": subjects,
        "record_chronology": chronology.model_dump(mode="json"),
        "scope": {
            "location": "서로 다른 두 위치의 속성 대비",
            "record_location": "같은 산책의 서로 다른 두 기록 위치에 연결된 공간 배경의 차이. 장소 자체의 시간 변화가 아님",
            "observation_time": "동일 대상의 서로 다른 자료 관측 시점에서 확인된 속성 대비",
        }[axis],
    }
