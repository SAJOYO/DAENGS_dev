"""Compose admitted facts into a writing scene; never infer geometry or select slots.

The finite policy distinguishes explanatory roles from acquisition order and
physical containment. No current source proves a park/cover relationship.
"""

from daengs_walk.diary.contracts.input import digest

VERSION = "diary-space-scene-v1"

# kind -> (purpose, subject, relation, scope). Provider names and distances never rank prose.
RULES = {
    "address": (
        "location_context",
        "record_location",
        "administrative_location",
        "administrative_area",
    ),
    "land_cover_at_query_point": (
        "background_basis",
        "record_location",
        "background_type",
        "query_point",
    ),
    "registered_park_point_distance": (
        "independent_surrounding",
        "record_location",
        "registered_point_proximity",
        "near_query_point",
    ),
    "registered_point": (
        "independent_surrounding",
        "record_location",
        "registered_point_distance",
        "near_query_point",
    ),
    "river_geometry": (
        "independent_surrounding",
        "record_location",
        "geometry_distance",
        "near_query_point",
    ),
    "registered_distribution_in_query_circle": (
        "area_context",
        "query_area",
        "registered_distribution",
        "whole_query_area",
    ),
    "area": ("area_context", "query_area", "registered_distribution", "whole_query_area"),
    "grid_weather": ("environment_context", "regional_grid", "prior_temperature", "regional_grid"),
    "regional_weather": (
        "environment_context",
        "observation_area",
        "regional_weather",
        "observation_area",
    ),
}
POLICY_REVISION = digest([VERSION, RULES])


def _kind(item):
    facts, role = item["facts"], item["role"]
    relation = facts.get("relation")
    kind = relation.get("kind") if isinstance(relation, dict) else None
    if role == "scene_address_reference":
        dong = facts.get("dong")
        return "address" if isinstance(dong, str) and dong.strip() else None
    if role == "grid_temperature_observation":
        return "grid_weather"
    if role == "regional_observation":
        return "regional_weather"
    expected = {
        "land_cover_at_query_point": "scene_geometry_distance",
        "registered_park_point_distance": "scene_registered_point_distance",
        "registered_distribution_in_query_circle": "scene_area_context",
    }
    if kind in expected:
        if role != expected[kind]:
            raise ValueError("space relation and role disagree")
        return kind
    if kind is not None:
        raise ValueError("unsupported space scene relation")
    if role == "scene_registered_point_distance":
        return "registered_point"
    if role == "scene_geometry_distance" and facts.get("reference") == "egis_river_polygon":
        return "river_geometry"
    if role == "scene_area_context":
        return "area"
    raise ValueError("unsupported space scene evidence")


def compile_scene(materials):
    """Caller supplies only the current stamp's admitted space/environment facts."""
    bindings, identities = [], set()
    for item in sorted(materials, key=lambda m: m["id"]):
        identity = item["id"]
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError("duplicate or invalid scene material")
        identities.add(identity)
        kind = _kind(item)
        if kind is None:
            continue
        purpose, subject, relation, scope = RULES[kind]
        binding = {
            "material_id": identity,
            "purpose": purpose,
            "subject": subject,
            "relation": relation,
            "scope": scope,
        }
        if purpose == "independent_surrounding":
            # Both facts may apply to the record point; that does not connect their objects.
            binding["background_link"] = "unconfirmed"
        bindings.append(binding)
    basis = [b["material_id"] for b in bindings if b["purpose"] == "background_basis"]
    return {
        "version": VERSION,
        "policy_revision": POLICY_REVISION,
        "materials_revision": digest(sorted(materials, key=lambda m: m["id"])),
        "background": {"basis_ids": basis},
        "bindings": bindings,
    }


def project_scene(materials, scene, aliases):
    """Recheck frozen decisions, then replace private IDs with invocation-local aliases."""
    if scene != compile_scene(materials):
        raise ValueError("space scene differs from admitted facts or policy")
    if set(aliases) != {b["material_id"] for b in scene["bindings"]}:
        raise ValueError("space scene and model material set differ")
    bindings = [{**b, "material_id": aliases[b["material_id"]]} for b in scene["bindings"]]
    return {
        "background": {"basis_ids": [aliases[k] for k in scene["background"]["basis_ids"]]},
        "bindings": bindings,
    }


def scene_fragment(scene, material_ids):
    """A detail lookup exposes only the precompiled bindings for returned materials."""
    selected = set(material_ids)
    bindings = [b for b in scene["bindings"] if b["material_id"] in selected]
    if {b["material_id"] for b in bindings} != selected:
        raise ValueError("space fragment refers to unknown material")
    return {
        "background": {
            "basis_ids": [k for k in scene["background"]["basis_ids"] if k in selected],
        },
        "bindings": [dict(b) for b in bindings],
    }


def require_background_citation(scene, evidence_ids, text):
    """Location identifies the record but cannot alone satisfy a background paragraph."""
    if scene is None or not text.strip():
        return
    descriptive = {
        b["material_id"] for b in scene["bindings"] if b["purpose"] != "location_context"
    }
    if not descriptive.intersection(evidence_ids):
        raise ValueError("space prose cites only location context")
