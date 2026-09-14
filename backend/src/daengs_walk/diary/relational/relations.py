"""Point comparisons and supported device intervals remain separate evidence."""

from daengs_walk.diary.board.activity import MEANINGS
from daengs_walk.diary.relational.contracts import SpatialRelation


def spatial_context(frame):
    result = {
        m["role"]: m
        for m in frame["space"].get("materials", [])
        if m["role"] in {"point_land_cover", "location_label"}
    }
    if frame.get("road_reference"):
        road = frame["road_reference"]
        result["road_address"] = {
            "id": road["id"],
            "role": "road_address",
            "material": {"road_nm": road["road_nm"]},
            "relation": road["scope"],
        }
    return result


def spatial_relations(frame, previous):
    current = spatial_context(frame)
    prior = spatial_context(previous) if previous else {}
    result = []
    for role in sorted(current.keys() | prior.keys()):
        before, after = prior.get(role), current.get(role)
        kind = (
            "unconfirmed"
            if after is None
            else "introduced"
            if before is None
            else "matching_points"
            if before["material"] == after["material"]
            else "point_difference"
        )
        sources = []
        if before:
            sources.append(previous["scene_id"] + ":" + before["id"])
        if after:
            sources.append(frame["scene_id"] + ":" + after["id"])
        result.append(
            SpatialRelation(
                id=f"r{len(result) + 1}",
                kind=kind,
                role=role,
                before=before["material"] if before else None,
                after=after["material"] if after else None,
                source_ids=tuple(sources),
            ).model_dump(mode="json")
        )
    return result


def movement_observations(frame, previous, catalog):
    """No dog actions or spatial labels; absence of a claim is not normal speed."""
    if (
        previous is None
        or catalog is None
        or frame["block"] is None
        or frame["block"] != previous["block"]
    ):
        return []
    left, at = previous["at_s"], frame["at_s"]
    result = []
    for claim in sorted(catalog.claims, key=lambda c: (c.get("event_s", c["start_s"]), c["id"])):
        if claim["block"] != frame["block"]:
            continue
        if "event_s" in claim:
            if not left <= claim["event_s"] < at:
                continue
            scope = {"at_pin_s": claim["event_s"] - at}
            when = f"현재 기록 {at - claim['event_s']:g}초 전"
        else:
            start, end = max(left, claim["start_s"]), min(at, claim["end_s"])
            if start >= end:
                continue
            scope = {"from_pin_s": start - at, "to_pin_s": end - at, "duration_s": end - start}
            when = f"현재 기록 {at - start:g}~{at - end:g}초 전"
        meaning = MEANINGS.get(claim["meaning"], claim["meaning"])
        result.append(
            {
                "id": f"motion{len(result) + 1}",
                "subject": "recording_device",
                "code": claim["meaning"],
                "meaning": meaning,
                "source_ids": [claim["id"]],
                **scope,
                "text": f"{when}: {meaning}",
                "origin": "computed_observation",
            }
        )
    return result
