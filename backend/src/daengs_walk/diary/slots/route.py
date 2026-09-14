"""Attach post-walk patterns to an existing scene, retaining support vs pivot meaning."""

from datetime import datetime

from daengs_walk.diary.board.models import RecordCore
from daengs_walk.diary.contracts.input import Point, digest
from daengs_walk.diary.contracts.slots import SlotDecision
from daengs_walk.diary.route.binding import verified_route
from daengs_walk.diary.route.patterns import normalize_route_patterns
from daengs_walk.diary.slots.sources import evidence, metres


def prepare_route_patterns(source, route, policy):
    if route is None:
        return None
    _, _, revision = verified_route(source, route)
    catalog = normalize_route_patterns(route.evidence, revision, policy.geometry)
    fixes = {fix.client_seq: fix for seg in route.evidence.segments for fix in (seg.a, seg.b)}
    runs = {
        run["pattern_run_index"]: tuple(
            fixes[seq] for seq in sorted(fixes) if run["from_seq"] <= seq <= run["to_seq"]
        )
        for run in catalog.shape_runs
    }
    return catalog, runs


def pattern_candidates(scene, prepared, policy, decisions):
    def reject(source_id, reason, eligibility="fail", **details):
        decisions.append(
            SlotDecision(
                source_id=source_id,
                part="motion",
                eligibility=eligibility,
                admission="excluded",
                reason=reason,
                details=details,
            )
        )

    if isinstance(scene.core, RecordCore) and scene.core.record.content.kind == "note":
        return []  # Special-moment original text has only spatial/environment supplements.
    if prepared is None:
        reject("route-pattern", "pattern_route_unavailable", "unknown")
        return []
    catalog, runs = prepared
    anchor, binding = scene.anchor, policy.route_patterns
    if anchor.point is None or anchor.position_state == "provisional":
        reject("route-pattern", "pattern_scene_unlocated", "unknown")
        return []
    if anchor.method != "estimated" and (
        anchor.location_at is None
        or abs((anchor.event_at - anchor.location_at).total_seconds()) > policy.location_age_s
    ):
        reject("route-pattern", "pattern_scene_location_stale", "unknown")
        return []
    matching = []
    for run_index, fixes in runs.items():
        if not fixes[0].at <= anchor.event_at <= fixes[-1].at:
            continue
        fix = min(fixes, key=lambda f: abs((f.at - anchor.event_at).total_seconds()))
        if (
            abs((fix.at - anchor.event_at).total_seconds()) <= policy.location_age_s
            and metres(anchor.point, Point(lat=fix.lat, lng=fix.lng)) <= policy.route_tolerance_m
        ):
            matching.append((run_index, fix))
    if len(matching) != 1:
        reject("route-pattern", "pattern_run_match_unknown", "unknown", matched_runs=len(matching))
        return []
    run_index, fix = matching[0]
    result = []
    for item in catalog.materials:
        support, pivot = item.support, item.anchor
        if support["pattern_run_index"] != run_index:
            continue
        start, end = (datetime.fromisoformat(support[k]) for k in ("started_at", "ended_at"))
        if not (
            start <= anchor.event_at <= end
            and support["from_seq"] <= fix.client_seq <= support["to_seq"]
        ):
            reject(item.id, "outside_pattern_support")
            continue
        if item.kind == "turn":
            seconds = abs((anchor.event_at - datetime.fromisoformat(pivot["at"])).total_seconds())
            distance = metres(anchor.point, Point(lat=pivot["lat"], lng=pivot["lng"]))
            if seconds > binding.turn_near_s or distance > binding.focus_radius_m:
                reject(item.id, "outside_turn_focus", time_delta_s=seconds, distance_m=distance)
                continue
            relation = {
                "kind": "near_observed_turn_vertex",
                "meaning": "이 장면 근처의 관측 꼭짓점에서 방향이 바뀜",
            }
        elif item.kind == "straight_run" and anchor.event_at == start:
            relation = {
                "kind": "starts_at_scene_point",
                "meaning": "이 장면 지점에서 시작하는 직선 구간",
            }
        elif item.kind == "straight_run" and anchor.event_at == end:
            relation = {
                "kind": "ends_at_scene_point",
                "meaning": "이 장면 지점에서 끝나는 직선 구간",
            }
        else:
            relation = {
                "kind": "scene_within_pattern_support",
                "meaning": "이 장면 지점을 포함하는 관측 구간",
            }
        facts = {
            "format": "route-pattern-material-v1",
            "kind": item.kind,
            "case_id": item.case_id,
            "subject": item.subject,
            "action_meaning": "not_inferred",
            "material": item.material,
            "relation": relation,
            "interpretation": " · ".join(item.material.values()),
            "temporal_relation": relation["meaning"],
        }
        result.append(
            evidence(
                "motion",
                "scene_motion",
                item.id,
                digest(item),
                item.id,
                facts,
                (0, 0),
                scope={
                    "scene_id": scene.id,
                    "anchor": anchor.model_dump(mode="json"),
                    "support": support,
                },
                diagnostics={
                    "source_revision": catalog.source_revision,
                    "dictionary_version": catalog.dictionary_version,
                    "support": support,
                    "pivot": pivot,
                    "metrics": item.metrics,
                    "quality": item.quality,
                    "binding_policy": binding.model_dump(mode="json"),
                },
            )
        )
    return result
