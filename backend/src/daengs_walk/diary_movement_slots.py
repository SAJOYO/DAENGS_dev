"""Bind composed movement to one visit and a bounded card explanation interval."""

from datetime import datetime

from .diary_input import Point
from .diary_movement import phases_for
from .diary_slot_sources import evidence, metres
from .diary_slots import SlotDecision


def movement_candidates(scene, scenes, catalog, policy, decisions):
    def reject(reason, **details):
        decisions.append(
            SlotDecision(
                source_id="movement",
                part="motion",
                eligibility="unknown",
                admission="excluded",
                reason=reason,
                details=details,
            )
        )
        return []

    anchor = scene.anchor
    if catalog is None:
        return reject("movement_route_unavailable")
    if anchor.point is None or anchor.position_state == "provisional":
        return reject("movement_scene_unlocated")
    if anchor.method != "estimated" and (
        anchor.location_at is None
        or abs((anchor.event_at - anchor.location_at).total_seconds()) > policy.location_age_s
    ):
        return reject("movement_scene_location_stale")
    at = (anchor.event_at - catalog.started_at).total_seconds()
    blocks = {}
    for n in catalog.nodes:
        blocks.setdefault(n["block"], []).append(n)
    matches = []
    for block, nodes in blocks.items():
        if not nodes[0]["elapsed_s"] <= at <= nodes[-1]["elapsed_s"]:
            continue
        nearest = min(nodes, key=lambda n: abs(n["elapsed_s"] - at))
        if (
            abs(nearest["elapsed_s"] - at) <= policy.location_age_s
            and metres(
                anchor.point, Point(lat=nearest["location"]["lat"], lng=nearest["location"]["lng"])
            )
            <= policy.route_tolerance_m
        ):
            matches.append(block)
    if len(matches) != 1:
        return reject("movement_visit_match_unknown", matched_blocks=len(matches))
    block = matches[0]
    times = sorted(
        {
            (s.anchor.event_at - catalog.started_at).total_seconds()
            for s in scenes
            if blocks[block][0]["elapsed_s"]
            <= (s.anchor.event_at - catalog.started_at).total_seconds()
            <= blocks[block][-1]["elapsed_s"]
        }
    )
    index = times.index(at)
    left = (times[index - 1] + at) / 2 if index else blocks[block][0]["elapsed_s"]
    right = (
        (at + times[index + 1]) / 2 if index + 1 < len(times) else blocks[block][-1]["elapsed_s"]
    )
    claims = []
    for claim in catalog.claims:
        if claim["block"] != block:
            continue
        if claim["meaning"].startswith("turn_"):
            pivot = claim["proof"]["anchor"]
            binding = policy.route_patterns
            max_seconds = binding.turn_near_s if binding else 20
            max_distance = binding.focus_radius_m if binding else 15
            if (
                abs((anchor.event_at - datetime.fromisoformat(pivot["at"])).total_seconds())
                > max_seconds
                or metres(anchor.point, Point(lat=pivot["lat"], lng=pivot["lng"])) > max_distance
            ):
                continue
        claims.append(claim)
    phases = phases_for(claims, left, right)
    if not phases:
        return reject("movement_no_supported_progress", start_s=left, end_s=right)
    used = {ref for phase in phases for ref in phase["claims"]}
    claims = [c for c in claims if c["id"] in used]
    facts = {
        "format": "diary-movement-material-v1",
        "subject": "recording_device",
        "action_meaning": "not_inferred",
        "scene_at_s": at,
        "window": {"start_s": left, "end_s": right},
        "phases": phases,
        "claims": sorted(claims, key=lambda c: c["id"]),
    }
    return [
        evidence(
            "motion",
            "scene_movement",
            "movement:" + catalog.source_revision,
            catalog.source_revision,
            ["movement", scene.id],
            facts,
            (0,),
            scope={"scene_id": scene.id, "block": block, "window": facts["window"]},
            diagnostics={"baseline": catalog.baseline, "policies": catalog.policies},
        )
    ]
