"""Meaning of validated spatial projections, independent of slot capacity."""

from daengs_walk.diary.contracts.input import digest

ROLES = {
    ("place-nearby-v1", "registered_location"): "scene_registered_point_distance",
    ("kakao-place-nearby-v1", "registered_location"): "scene_registered_point_distance",
    ("kakao-address-v1", "coordinate_reverse_geocoded_legal_dong"): "scene_address_reference",
    ("public-park-nearby-v1", "registered_park_point"): "scene_registered_point_distance",
    ("public-river-nearby-v1", "egis_river_polygon"): "scene_geometry_distance",
    ("public-commerce-nearby-v1", "registered_business_composition"): "scene_area_context",
    ("sgis-dong-v1", "coordinate_reverse_geocoded_dong"): "scene_address_reference",
}


def spatial_claim(piece, saved):
    facts = piece.facts
    role = ROLES.get((piece.schema_version, facts.get("reference")))
    if role is None:
        raise ValueError("unsupported_spatial_relation")
    scope = {
        "target": saved.target.model_dump(mode="json"),
        "point": saved.query_point.model_dump(mode="json"),
        "schema": piece.schema_version,
        "temporal_basis": saved.temporal_basis,
        "valid_from": saved.valid_from.isoformat() if saved.valid_from else None,
        "valid_until": saved.valid_until.isoformat() if saved.valid_until else None,
    }
    entity = facts["source_ref"]
    if role in {"scene_address_reference", "scene_area_context"}:
        # Address codes/catalog hashes are assertion values, not competing subjects.
        entity = {"source": saved.provider, "subject": role, "point": scope["point"]}
    if role == "scene_area_context":
        scope["radius_m"] = facts["radius_m"]
    if role in {"scene_registered_point_distance", "scene_geometry_distance"}:
        rank = (float(facts["distance_m"]),)
    else:
        rank = ()  # Address has no distance; footprint radius is not proximity.
    return role, digest(entity), scope, rank
