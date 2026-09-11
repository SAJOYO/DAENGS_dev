"""Finite adapters from DEV's saved context and canonical motion to part-slot evidence."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from daengs_walk.diary_background import piece_identity, piece_rank, project_background
from daengs_walk.diary_board import ObservationCore, RecordCore
from daengs_walk.diary_input import DiaryContract, Identifier, Instant, Point, digest, material_ref
from daengs_walk.diary_observations import build_observation_pool
from daengs_walk.diary_slots import SlotDecision, SlotEvidence
from daengs_walk.storyboard_input import route_nodes
from daengs_walk.storyboard_selection import distance


class RegionalWeather(DiaryContract):
    """Adapter input, not an acquisition provider. Circle is source-declared coverage."""

    format: Literal["regional-weather-v1"]
    source_ref: Identifier
    area_center: Point
    area_radius_m: float = Field(gt=0, le=100_000)
    valid_from: Instant
    valid_until: Instant
    temperature_c: float | None = Field(default=None, ge=-90, le=60)
    wind_mps: float | None = Field(default=None, ge=0, le=120)
    precipitation_mm: float | None = Field(default=None, ge=0, le=1000)
    sky: Literal["clear", "cloudy", "overcast"] | None = None

    @model_validator(mode="after")
    def supported(self):
        if self.valid_from >= self.valid_until or all(
            v is None for v in (self.temperature_c, self.wind_mps, self.precipitation_mm, self.sky)
        ):
            raise ValueError("weather needs a nonempty interval and an observation")
        return self


def metres(a, b):
    return distance((a.lat, a.lng), (b.lat, b.lng))


def evidence(part, role, source_id, version, identity, facts, rank):
    return SlotEvidence(
        id="slot:" + digest([part, role, source_id, version, facts]),
        part=part,
        role=role,
        source_id=source_id,
        source_version=version,
        identity=identity,
        facts=facts,
        rank=rank,
    )


def verified_motion(source, route):
    if route is None:
        return (), {}
    pool = build_observation_pool(route.evidence, source.route)
    expected = {o.id: o for o in pool.observations}
    if any(expected.get(o.id) != o for o in source.observations):
        raise ValueError("motion differs from canonical replay")
    blocks = {}
    for node in route_nodes(route.evidence):
        node["at"] = datetime.fromisoformat(node["observation"]["at"])
        blocks.setdefault(node["block"], []).append(node)
    return source.observations, blocks


def candidates_for_scene(source, scene, policy, motion, blocks):
    candidates, decisions = [], []

    def reject(part, source_id, reason, eligibility="fail"):
        decisions.append(
            SlotDecision(
                source_id=source_id,
                part=part,
                eligibility=eligibility,
                admission="excluded",
                reason=reason,
            )
        )

    anchor = scene.anchor
    core = (
        scene.core.record
        if isinstance(scene.core, RecordCore)
        else scene.core.observation
        if isinstance(scene.core, ObservationCore)
        else None
    )
    located = anchor.point is not None and anchor.position_state != "provisional"
    fresh = located and (
        anchor.method == "estimated"
        or (
            anchor.location_at is not None
            and abs((anchor.event_at - anchor.location_at).total_seconds()) <= policy.location_age_s
        )
    )
    selected = set(source.selected_background_ids)
    for saved in sorted(source.backgrounds, key=lambda b: b.id):
        # Distances supplied for another core cannot migrate to this scene.
        if core is None or saved.target != material_ref(core):
            continue
        part = "environment" if saved.tags == ("environment",) else "space"
        if saved.id not in selected or saved.status not in {"known", "partial"}:
            reject(part, saved.id, saved.reason or saved.status, "unknown")
            continue
        if not fresh:
            reject(part, saved.id, "scene_location_unavailable_or_stale", "unknown")
            continue
        if part == "space":
            projection = project_background(saved, core)
            if projection.reason:
                reject(part, saved.id, projection.reason, "unknown")
            for row in projection.rejected_rows:
                reject(part, saved.id, f"invalid_provider_row:{row}")
            for piece in projection.pieces:
                facts = piece.facts
                if "distance_m" in facts and facts["distance_m"] > policy.space_radius_m:
                    reject(part, saved.id, "outside_space_radius")
                    continue
                # An area aggregate retains its footprint; radius is not point distance.
                role = (
                    "scene_registered_point_distance"
                    if "distance_m" in facts
                    else "scene_area_context"
                    if piece.kind == "space_relation"
                    else "scene_address_reference"
                )
                candidates.append(
                    evidence(
                        part,
                        role,
                        saved.id,
                        digest(saved),
                        str(piece_identity(piece)),
                        {**facts, "retrieved_at": saved.retrieved_at.isoformat()},
                        tuple(piece_rank(piece, saved)[:2]),
                    )
                )
        else:
            if saved.provider != "weather-observation" or saved.temporal_basis not in {
                "event_observation",
                "source_observation",
            }:
                reject(part, saved.id, "unsupported_weather_observation", "unknown")
                continue
            try:
                weather = RegionalWeather.model_validate(saved.payload)
            except ValueError:
                reject(part, saved.id, "invalid_weather_payload", "unknown")
                continue
            if not weather.valid_from <= anchor.event_at < weather.valid_until:
                reject(part, saved.id, "outside_weather_interval")
                continue
            if (
                saved.valid_from is not None
                and not saved.valid_from <= anchor.event_at < saved.valid_until
            ):
                reject(part, saved.id, "outside_source_interval")
                continue
            if metres(anchor.point, weather.area_center) > weather.area_radius_m:
                reject(part, saved.id, "outside_weather_area")
                continue
            candidates.append(
                evidence(
                    part,
                    "regional_observation",
                    saved.id,
                    digest(saved),
                    weather.source_ref,
                    {
                        **weather.model_dump(mode="json", exclude_none=True),
                        "relation": "regional_observation_not_personal_sensation",
                        "retrieved_at": saved.retrieved_at.isoformat(),
                    },
                    (-weather.valid_from.timestamp(), -saved.retrieved_at.timestamp()),
                )
            )

    matching = []
    if fresh:
        for block, nodes in blocks.items():
            if nodes[0]["at"] <= anchor.event_at <= nodes[-1]["at"]:
                nearest = min(nodes, key=lambda n: abs((n["at"] - anchor.event_at).total_seconds()))
                if (
                    abs((nearest["at"] - anchor.event_at).total_seconds()) <= policy.location_age_s
                    and metres(
                        anchor.point,
                        Point.model_validate(
                            {
                                "lat": nearest["location"]["lat"],
                                "lng": nearest["location"]["lng"],
                            }
                        ),
                    )
                    <= policy.route_tolerance_m
                ):
                    matching.append(block)
    for item in motion:
        gap = (anchor.event_at - item.ended_at).total_seconds()
        if anchor.event_at < item.started_at or gap > policy.motion_gap_s:
            continue
        if len(matching) != 1:
            reject("motion", item.id, "scene_route_match_unknown", "unknown")
            continue
        nodes = blocks[matching[0]]
        if not nodes[0]["at"] <= item.started_at <= item.ended_at <= nodes[-1]["at"]:
            reject("motion", item.id, "canonical_continuity_break")
            continue
        role = "scene_motion" if gap <= 0 else "before_scene_motion"
        facts = {
            "kind": item.kind,
            "subject": item.subject,
            "action_meaning": item.action_meaning,
            "started_at": item.started_at.isoformat(),
            "ended_at": item.ended_at.isoformat(),
            "duration_s": (item.ended_at - item.started_at).total_seconds(),
            "before_scene_s": max(0, gap),
            "continuity_block": matching[0],
            "continuity_basis": "canonical_accepted_segments",
            "analysis_id": item.analysis_id,
        }
        candidates.append(
            evidence(
                "motion",
                role,
                item.id,
                item.version,
                item.id,
                facts,
                (max(0, gap), -facts["duration_s"]),
            )
        )
    return candidates, decisions
