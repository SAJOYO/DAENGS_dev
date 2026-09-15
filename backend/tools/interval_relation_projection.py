"""Experimental interval ledger; unique raw claims, then scene ownership. No prose templates."""
from copy import deepcopy
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt


def distance(a, b):
    x, y = radians(a["lat"]), radians(b["lat"])
    d = sin((y-x)/2)**2 + cos(x)*cos(y)*sin(radians(b["lng"]-a["lng"])/2)**2
    return 6371000 * 2 * asin(min(1, sqrt(d)))


def ledger(frames, source):
    from daengs_walk.value_contracts import digest
    claims = {}
    for frame in frames:
        for row in frame.get("relation_observations") or []:
            claim = row["source_claim"]
            previous = claims.setdefault(claim["id"], deepcopy(claim))
            if previous != claim:
                raise ValueError("one source claim has incompatible content")
    start = source.started_at
    relations, bindings = [], {}
    for claim in sorted(claims.values(), key=lambda c: (c.get("event_s", c["end_s"]), c["id"])):
        relation_id = "segment:" + digest([source.route.model_dump(mode="json"), claim])
        is_event = "event_s" in claim
        row = {"id": relation_id, "subject": "recording_device", "kind": claim["meaning"],
               "support_started_at": (start + timedelta(seconds=claim["start_s"])).isoformat(),
               "support_ended_at": (start + timedelta(seconds=claim["end_s"])).isoformat(),
               "representative_event_at": (start + timedelta(seconds=claim["event_s"])).isoformat() if is_event else None,
               "event_time_basis": "detector_estimate" if is_event else None,
               "allocation_at": (start + timedelta(seconds=claim.get("event_s", claim["end_s"]))).isoformat(),
               "scope": "observed_route_only; does_not_establish_road_identity_or_dog_action",
               "source_status": claim.get("proof", {}).get("status")}
        if claim["meaning"] == "retrace":
            metrics = claim["proof"]["metrics"]
            row["prior_path"] = {key: metrics[key] for key in
                                 ("past_from_seq", "past_to_seq", "matched_reverse_m")}
        relations.append(row)
        bindings[relation_id] = {"source_revision": source.revision(), "route": source.route.model_dump(mode="json"), "claim": claim}
    first, last = frames[0]["anchor"], frames[-1]["anchor"]
    if first["event_at"] == source.started_at.isoformat().replace("+00:00", "Z") and last["point"] and first["point"]:
        if datetime.fromisoformat(last["event_at"].replace("Z", "+00:00")) == source.ended_at:
            gap = distance(first["point"], last["point"])
            tolerance = max(1, (first.get("accuracy_m") or 0) + (last.get("accuracy_m") or 0))
            if gap <= tolerance:
                key = "segment:" + digest([source.route.model_dump(mode="json"), first, last, "endpoint_proximity"])
                relations.append({"id": key, "subject": "recording_device", "kind": "end_near_start",
                                  "start_anchor": first, "end_anchor": last, "distance_m": gap,
                                  "tolerance_m": tolerance, "allocation_at": source.ended_at.isoformat(),
                                  "scope": "session_endpoints; home_and_motive_unknown"})
                bindings[key] = {"source_revision": source.revision(), "route": source.route.model_dump(mode="json"), "anchors": [first, last]}
    return relations, bindings


def allocate(relations, anchors):
    ordered = sorted(anchors, key=lambda a: a["seconds"])
    result = {a["id"]: [] for a in ordered}
    for relation in relations:
        at = datetime.fromisoformat(relation["allocation_at"])
        owner = next((a for a in ordered if a["at"] >= at), None)
        if owner is not None:
            result[owner["id"]].append(relation["id"])
    return result
