"""Project admitted-before-budget spatial facts, preserving comparison attributes.

Reuse the existing Korean wording; source diagnostics supply identity and scope,
not prose instructions. Raw geometry and shop ID lists remain in eligible_evidence.
"""

from copy import deepcopy

from daengs_walk.diary.relational.scene_comparison_contracts import SceneFact
from daengs_walk.diary.space.semantics import LAND_WORDS, material
from daengs_walk.value_contracts import digest

FAMILIES = {"land_cover": "land_cover", "park": "surrounding_object", "commerce": "area_context"}


def project_scene_fact(scene_id, point, evidence, backgrounds):
    facts = evidence["facts"]
    source = facts.get("source")
    if facts.get("format") != "space-material-v1" or source not in FAMILIES:
        return None
    diagnostics = evidence["diagnostics"]
    scope, support = diagnostics["scope"], diagnostics["support"]
    query = scope.get("point" if source == "commerce" else "query_point")
    if point is None or query != point:
        raise ValueError("scene fact belongs to a different query point")
    projected = material({"role": evidence["role"], "facts": facts})
    value = deepcopy(projected["material"])
    provider = "public-normalized-" + source
    subject, reference = None, None
    if source == "land_cover":
        value = {k: LAND_WORDS.get(v, v) for k, v in value.items()}
        value["classification"] = deepcopy(facts["material"])
        value["layer"] = support["layer"]
        subject = f"{provider}:{support['layer']}:{support['source_id']}"
        reference = support.get("image_date")
        kind = "record_point"
    elif source == "park":
        value = {
            "name": facts["material"]["공원명"],
            "park_type": facts["material"]["공원종류"],
            "registered_point": scope["point"],
            "distance_m": facts["relation"]["distance_m"],
            "query_radius_m": scope["query_radius_m"],
            "area_m2": support.get("area_m2"),
            "reference_dates": support.get("reference_dates", []),
            "catalog_complete": support["source_complete"],
        }
        subject = f"{provider}:{support['source_id']}"
        dates = support.get("reference_dates", [])
        reference = dates[0] if len(dates) == 1 else None
        kind = "registered_point"
    else:
        value = {
            "composition": value,
            "query": deepcopy(scope),
            **{
                k: deepcopy(support[k])
                for k in (
                    "registered_count",
                    "groups",
                    "registered_sites",
                    "spread_rms_m",
                    "nearest_registered_point_m",
                    "centroid_distance_m",
                    "reference_month",
                    "beverage_count",
                    "classification_policy",
                    "source_hash",
                )
                if k in support
            },
        }
        subject = f"{provider}:area:{digest(scope)}"
        reference = support.get("reference_month")
        kind = "query_area"
    # Multiple supporting source versions survive deduplication upstream.
    sources = evidence.get("sources") or [
        {
            "source_id": evidence["source_id"],
            "source_version": evidence["source_version"],
        }
    ]
    saved = [backgrounds[s["source_id"]] for s in sources if s["source_id"] in backgrounds]
    fetched = {s.get("retrieved_at") for s in saved}
    refs = tuple(sorted({f"{s['source_id']}@{s['source_version']}" for s in sources}))
    return SceneFact(
        id=f"{scene_id}:{evidence['id']}",
        family=FAMILIES[source],
        value=value,
        scope={"kind": kind, "description": projected["relation"], "coverage_key": digest(scope)},
        subject_key=subject,
        source_refs=refs,
        retrieved_at=next(iter(fetched)) if len(fetched) == 1 else None,
        reference_date=str(reference) if reference is not None else None,
        time_meaning=projected.get("time_meaning", "자료의 관측 시점은 미확인"),
    )
