"""Finite spatial materials -> existing claim/admission contracts and prose meaning."""

from daengs_walk.diary.contracts.input import SavedBackground, digest
from daengs_walk.diary.space.cases import CASES
from daengs_walk.diary.space.materials import SpaceMaterials

ROLES = {
    "commerce": "scene_area_context",
    "park": "scene_registered_point_distance",
    "land_cover": "scene_geometry_distance",
}


def space_candidates(saved, anchor, scene_scope, policy, reject):
    from daengs_walk.diary.slots.sources import evidence

    try:
        saved = SavedBackground.model_validate(saved.model_dump(mode="json"))
        value = SpaceMaterials.model_validate(saved.payload)
        if value.dictionary_version != digest(CASES) or value.point != anchor.point:
            raise ValueError("dictionary or query changed")
        if saved.query_point != value.point:
            raise ValueError("background query changed")
        for item in value.materials:
            data = item.model_dump(mode="json", exclude={"id"})
            if item.id != "space:" + digest(data):
                raise ValueError("material changed after normalization")
            query = item.scope.get("point" if item.source == "commerce" else "query_point")
            if query != value.point.model_dump():
                raise ValueError("material scope belongs to another query")
    except ValueError:
        reject("space", saved.id, "invalid_normalized_space", "unknown")
        return []
    for item in value.audit:
        if item["reason"] != "not_supplied":
            reject("space", saved.id, str(item["reason"]), "unknown", source=item["source"])
    result = []
    for item in value.materials:
        role = ROLES[item.source]
        scope, support = item.scope, item.support
        # Scope is supplied by normalization, not inferred by the writer.
        if item.source == "commerce":
            relation = {
                "kind": "registered_distribution_in_query_circle",
                "radius_m": scope["radius_m"],
                "nearest_registered_point_m": support["nearest_registered_point_m"],
                "centroid_distance_m": support["centroid_distance_m"],
            }
            subject, rank = {"query": value.point.model_dump()}, ()
        elif item.source == "park":
            relation = {"kind": "registered_park_point_distance", "distance_m": scope["distance_m"]}
            subject, rank = support["source_id"], (scope["distance_m"],)
        else:
            relation = {"kind": "land_cover_at_query_point", "distance_m": 0}
            subject, rank = [support["layer"], support["source_id"]], (0,)
        facts = {
            "format": "space-material-v1",
            "source": item.source,
            "case_ids": list(item.case_ids),
            "material": item.material,
            "relation": relation,
            "temporal_basis": saved.temporal_basis,
            **({"distance_m": relation["distance_m"]} if "distance_m" in relation else {}),
        }
        result.append(
            evidence(
                "space",
                role,
                saved.id,
                digest(saved),
                {"source": item.source, "subject": subject},
                facts,
                rank,
                scope={**scene_scope, "material_scope": scope},
                diagnostics={
                    "material_id": item.id,
                    "dictionary_version": value.dictionary_version,
                    "scope": scope,
                    "support": support,
                    "actual": relation.get("distance_m"),
                    "limit": policy.space_radius_m,
                    "unit": "m",
                },
            )
        )
    return result


def writing_facts(item):
    """Keep application relations; internal geometry/counts never become prose instructions."""
    if item.facts.get("format") == "diary-movement-material-v1":
        from daengs_walk.diary.board.activity import activity_projection

        payload, _ = activity_projection({"movement": [{"id": item.id, "facts": item.facts}]})
        return {**payload, "subject": "recording_device", "action_meaning": "not_inferred"}
    if item.facts.get("format") == "route-pattern-material-v1":
        return {
            key: item.facts[key] for key in ("material", "relation", "subject", "action_meaning")
        }
    if item.facts.get("format") != "space-material-v1":
        return item.facts
    return {key: item.facts[key] for key in ("material", "relation", "temporal_basis")}
