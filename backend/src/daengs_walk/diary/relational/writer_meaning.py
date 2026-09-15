"""Writing semantics only. Never serialize provenance or upstream explanation strings.

Every nested field is selected explicitly. Scope names the supported relationship;
missing claims are omitted, not enumerated as unknown or false.
"""

from daengs_walk.diary.relational.brief_contracts import NarrativeFact
from daengs_walk.diary.relational.relation_vocabulary import ENDPOINT_WORDS


def instant(value):
    return value.isoformat() if value is not None else None


def vocabulary(value, meanings):
    if value not in meanings:
        raise ValueError("unsupported writing meaning")
    return meanings[value]


def present(**fields):
    """Omit empty optional fields only. Zero and false are real values.

    This is not a recursive JSON scrubber. Meaningful empty collections, such as
    route gaps and the citation allowlist, are explicitly emitted by their owner.
    """
    return {k: v for k, v in fields.items() if v is not None and v != {} and v != [] and v != ""}


def walk_view(position):
    return {
        "started_at": instant(position.timeline.started_at),
        "ended_at": instant(position.timeline.ended_at),
        "selected_scene_count": position.selected_scene_count,
    }


def position_view(position):
    return {
        "scene_id": position.scene_id,
        "selected_scene_number": position.selected_scene_number,
        "recorded_at": instant(position.recorded_at),
        "phase": position.phase,
        "seconds_since_start": position.seconds_since_start,
    }


def anchor_view(anchor):
    return present(
        position=position_view(anchor.position),
        position_basis=anchor.position_basis if anchor.point is not None else None,
        accuracy_m=anchor.accuracy_m,
    )


def event_anchor_view(anchor):
    return present(
        event_at=instant(anchor.event_at),
        time_basis=vocabulary(
            anchor.time_basis,
            {
                "recorded_at": "event_time",
                "photo_capture": "photo_time",
                "session_fallback": "session_time",
                "route_observation": "walk_time",
            },
        ),
        location_at=instant(anchor.location_at) if anchor.location_at != anchor.event_at else None,
        position_basis=anchor.method if anchor.point is not None else None,
        accuracy_m=anchor.accuracy_m,
    )


def fact_view(fact):
    m = fact.meaning
    if m.kind == "road":
        meaning = {"kind": m.kind, "name": m.name}
        scope = {"kind": "address_reference"}
    elif m.kind == "land_cover":
        meaning = {"kind": m.kind, "label": m.label}
        scope = {"kind": "point_land_cover"}
    elif m.kind == "surrounding_object":
        meaning = present(
            kind=m.kind,
            name=m.name,
            park_type=m.park_type,
            distance_m=m.distance_m,
            area_m2=m.area_m2,
        )
        scope = {"kind": "object_reference_point"}
    else:
        # Translate a closed normalized vocabulary, never replace arbitrary prose.
        distribution = vocabulary(
            m.spatial_distribution,
            {
                None: None,
                "등록 지점이 적음": "sparse",
                "등록 지점이 모여 있음": "clustered",
                "등록 지점이 흩어져 있음": "dispersed",
            },
        )
        meaning = present(
            kind=m.kind,
            business_mix=m.business_mix,
            spatial_distribution=distribution,
            radius_m=m.radius_m,
        )
        scope = {"kind": "surrounding_area", "distribution_of": "business_locations"}
    return present(
        id=fact.id,
        scene_id=fact.scene_id,
        meaning=meaning,
        scope=scope,
        material_time=present(as_of=fact.reference_date, measured_at=instant(fact.observed_at)),
    )


def relation_view(relation, *, legacy_v2=False):
    return {
        "id": relation.id,
        "family": relation.family,
        "axis": "surrounding_area" if relation.axis == "query_area" else relation.axis,
        **(
            {"result": relation.result}
            if legacy_v2
            else {"relationship": ENDPOINT_WORDS[relation.result]}
        ),
        "earlier_evidence_ids": list(relation.earlier_evidence_ids),
        "current_evidence_ids": list(relation.current_evidence_ids),
        "scope": {"kind": "endpoint_comparison"},
    }


def route_view(route):
    if route is None:
        return None
    return {
        "id": route.id,
        "status": route.status,
        "started_at": instant(route.started_at),
        "ended_at": instant(route.ended_at),
        "elapsed_seconds": route.elapsed_seconds,
        "covered_seconds": route.observed_seconds,
        "distance_m": route.observed_distance_m,
        "moving_distance_m": route.moving_distance_m,
        "gaps": [
            {"start": instant(g.start), "end": instant(g.end)} for g in route.uncovered_intervals
        ],
        "scope": {"kind": "walk_interval", "spatial_support": "endpoints"},
    }


def connection_view(connection):
    if connection is None:
        return None
    return present(
        earlier_scene_id=connection.earlier_scene_id,
        current_scene_id=connection.current_scene_id,
        elapsed_seconds=connection.elapsed_seconds,
        route_evidence_ids=list(connection.route_evidence_ids),
    )


def event_view(event):
    return {
        "id": event.id,
        "scene_id": event.scene_id,
        "actor": present(entity_type="dog", pet_id=event.actor.pet_id, name=event.actor.name),
        "behavior": event.behavior,
        "anchor": event_anchor_view(event.anchor),
    }


def event_context_view(context):
    e = context.evidence
    if isinstance(e, NarrativeFact):
        evidence = fact_view(e)
    else:
        evidence = {
            "id": e.id,
            "meaning": e.meaning,
            "relative_to_event": present(
                start_seconds=e.from_pin_s, end_seconds=e.to_pin_s, event_seconds=e.event_at_pin_s
            ),
            "relationship": "walk_movement_coincident_with_event",
        }
    return {"for_event_id": context.for_event_id, "kind": context.kind, "evidence": evidence}
