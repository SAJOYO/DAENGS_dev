"""Assemble full eligible scene facts and display header without acquisition or LLM."""

from copy import deepcopy

from daengs_backend.services.walk_diary.preparation.scene_facts import FAMILIES, project_scene_fact
from daengs_walk.diary.relational.scene_comparison_contracts import (
    SceneCardHeader,
    SceneFact,
    SceneSnapshot,
)
from daengs_walk.diary.space.road import road_name
from daengs_walk.diary.space.semantics import material
from daengs_walk.value_contracts import digest


def _collection(backgrounds):
    states = {family: "unknown" for family in ("road", *FAMILIES.values())}
    reasons = {family: [] for family in states}
    grouped = {family: [] for family in states}
    for saved in backgrounds:
        source = saved["provider"].removeprefix("public-normalized-")
        if source not in FAMILIES:
            continue
        family = FAMILIES[source]
        # A successful envelope can still contain failed/partial normalization.
        audit = [
            a["reason"]
            for a in (saved.get("payload") or {}).get("audit", [])
            if a.get("source") == source and a.get("reason") != "not_supplied"
        ]
        reasons[family].extend(audit + ([saved["reason"]] if saved.get("reason") else []))
        state = {
            "known": "complete",
            "partial": "partial",
            "empty": "empty",
            "unavailable": "unknown",
            "not_requested": "not_requested",
        }[saved["status"]]
        if audit and state == "complete":
            # This reason is emitted only after complete, valid commerce rows
            # have been filtered to the query footprint. Other audit failures
            # must not be swallowed by a coincident empty-result marker.
            no_shops = source == "commerce" and set(audit) == {"no_registered_shops_in_footprint"}
            has_materials = any(
                m.get("source") == source for m in (saved.get("payload") or {}).get("materials", [])
            )
            state = "empty" if no_shops and not has_materials else "partial"
        grouped[family].append(state)
    for family, values in grouped.items():
        if values:
            states[family] = values[0] if len(set(values)) == 1 else "partial"
    return states, reasons


def _road(scene_id, point, snapshots):
    matching = [
        s
        for s in snapshots
        if point is not None and s.get("point") == point and s.get("addr_type") == 10
    ]
    candidates = []
    for saved in matching:
        response = saved.get("response", {})
        rows = response.get("result", [])
        if response.get("errCd") != 0 or not isinstance(rows, list) or len(rows) != 1:
            continue
        if not isinstance(rows[0], dict):
            continue
        name = rows[0].get("road_nm")
        # Numbers inside a road name (e.g. 양재천로3길) are valid; building numbers are not.
        if road_name(name) is not None:
            candidates.append((name.strip(), saved))
    if not candidates:
        if matching and all(s.get("response") == {"errCd": 0, "result": []} for s in matching):
            return None, "empty", "road_query_empty; not_proof_of_road_absence"
        if matching and all(s.get("status") == "not_requested" for s in matching):
            return None, "not_requested", "road_not_requested"
        return None, "unknown", "road_response_unusable" if matching else "road_not_supplied"
    if len({name for name, _ in candidates}) != 1:
        return None, "partial", "conflicting_road_names"
    refs = tuple(sorted({"sgis:road:" + digest(s) for _, s in candidates}))
    times = {s.get("retrieved_at") for _, s in candidates}
    fact = SceneFact(
        id=f"{scene_id}:road:{digest(refs)}",
        family="road",
        value={"name": candidates[0][0]},
        source_refs=refs,
        scope={
            "kind": "record_point",
            "coverage_key": digest(point),
            "description": "좌표에 대응한 주소의 도로명. 실제 통과·진입 도로는 미확인",
        },
        retrieved_at=next(iter(times)) if len(times) == 1 else None,
        time_meaning="조회 시점의 주소 대응. 도로의 관측 시점은 미확인",
    )
    return fact, "complete" if len(candidates) == len(matching) else "partial", "road_name_only"


def assemble_scene_snapshot(frame, *, backgrounds=(), road_snapshots=()):
    """Only eligible_evidence is consumed; selected materials are not a fallback.

    backgrounds must already be restricted to this scene's target. Missing source
    status is unknown, not an empty result or an unrequested query.
    """
    anchor, scene_id = frame["anchor"], frame["scene_id"]
    point = anchor.get("point")
    saved = {b["id"]: b for b in backgrounds}
    states, reasons = _collection(backgrounds)
    facts, dongs, weather = [], set(), []
    addresses = []
    for evidence in frame["eligible_evidence"]:
        fact = project_scene_fact(scene_id, point, evidence, saved)
        if fact is not None:
            facts.append(fact)
            if states[fact.family] in {"unknown", "not_requested", "empty"}:
                states[fact.family] = "partial"
            if fact.family == "surrounding_object" and not fact.value["catalog_complete"]:
                states[fact.family] = "partial"
        elif evidence["role"] == "scene_address_reference":
            projected = material({"role": evidence["role"], "facts": evidence["facts"]})
            if projected:
                dong = projected["material"]["dong"]
                dongs.add(dong)
                values = evidence["facts"]
                address = {"dong": dong}
                for key in ("sido", "sigungu", "address_type"):
                    value = values.get(key)
                    address[key] = value.strip() or None if isinstance(value, str) else None
                if address not in addresses:
                    addresses.append(address)
        elif evidence["role"] in {"grid_temperature_observation", "regional_observation"}:
            weather.append(
                {"source_id": evidence["source_id"], "facts": deepcopy(evidence["facts"])}
            )
    road, states["road"], reason = _road(scene_id, point, road_snapshots)
    reasons["road"].append(reason)
    if road:
        facts.append(road)
    for family in FAMILIES.values():
        if not any(f.family == family for f in facts) and states[family] == "complete":
            states[family] = "partial"
            reasons[family].append("no_eligible_facts; not_proof_of_empty_result")
    snapshot = SceneSnapshot(
        scene_id=scene_id,
        walk_id=frame["walk_session"],
        recorded_at=anchor["event_at"],
        point=point,
        accuracy_m=anchor.get("accuracy_m"),
        position_basis=anchor.get("method", "none") if point is not None else "none",
        facts=tuple(sorted(facts, key=lambda f: f.id)),
        collection=states,
        collection_reasons={k: tuple(sorted(set(v))) for k, v in reasons.items()},
    )
    header = SceneCardHeader(
        scene_id=scene_id,
        dong=next(iter(dongs)) if len(dongs) == 1 else None,
        weather={"observations": weather} if weather else None,
        administrative_address=addresses[0] if len(addresses) == 1 else None,
    )
    return snapshot, header


def validate_scene_snapshot_bindings(prepared_snapshot):
    """Rebuild derived data from retained eligible facts and source envelopes.

    This checks internal consistency, not authenticity of an entirely replaced
    input bundle. Older v6 bundles without the new comparison feature still read.
    """
    from daengs_walk.diary.contracts.input import SavedBackground
    from daengs_walk.diary.relational.relations.registry import collect_spatial_comparisons

    frames = prepared_snapshot["frames"]
    fields = {"scene_snapshot", "card_header", "spatial_comparison_slots", "scene_background_ids"}
    if not prepared_snapshot.get("scene_comparison_version") and not any(
        fields & frame.keys() for frame in frames
    ):
        return
    if prepared_snapshot.get("scene_comparison_version") != "scene-comparison-v1":
        raise ValueError("comparison source manifest missing; prepare scenes again")
    sources = prepared_snapshot.get("scene_backgrounds")
    if sources is None:
        raise ValueError("comparison source manifest missing")
    if not isinstance(sources, dict):
        raise TypeError("comparison source manifest must be a mapping")
    for key, saved in sources.items():
        value = SavedBackground.model_validate(saved)
        if key != value.id:
            raise ValueError("comparison source identity changed")
    previous = None
    for frame in frames:
        if not fields <= frame.keys():
            raise ValueError("incomplete scene comparison frame")
        ids = frame["scene_background_ids"]
        if len(set(ids)) != len(ids) or not set(ids) <= sources.keys():
            raise ValueError("comparison source references changed")
        rebuilt, header = assemble_scene_snapshot(
            frame,
            backgrounds=[sources[key] for key in ids],
            road_snapshots=prepared_snapshot.get("road_snapshots", ()),
        )
        if rebuilt.model_dump(mode="json") != frame["scene_snapshot"]:
            raise ValueError("scene snapshot does not match eligible evidence and sources")
        # Pre-address snapshots intentionally retain their original dong-only header.
        excluded = {"administrative_address"} - frame["card_header"].keys()
        if header.model_dump(mode="json", exclude=excluded) != frame["card_header"]:
            raise ValueError("card header does not match eligible evidence")
        if frame.get("planning_contract") == "scene-comparison-plan-v1":
            from daengs_walk.diary.relational.current_action import current_background

            if frame["space"]["materials"] != current_background(frame["scene_snapshot"]):
                raise ValueError("current action background differs from scene snapshot")
            road = next((f for f in rebuilt.facts if f.family == "road"), None)
            expected_road = (
                {"id": road.id, "road_nm": road.value["name"], "scope": road.scope.description}
                if road
                else None
            )
            if frame.get("road_reference") != expected_road:
                raise ValueError("current action road differs from scene snapshot")
        comparisons = collect_spatial_comparisons(rebuilt, previous)
        if comparisons != frame["spatial_comparison_slots"]:
            raise ValueError("spatial comparison slots do not match scene snapshots")
        previous = rebuilt
