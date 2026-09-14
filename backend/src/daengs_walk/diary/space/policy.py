"""Source-specific application rules for normalized spatial memory, before capacity.

This experimental policy keeps a fixed commerce footprint. It neither relocates
that sample nor concludes that the walker is inside a cluster of registrations.
"""

from typing import Literal

from pydantic import Field, JsonValue

from daengs_walk.diary.contracts.canonical import normalize
from daengs_walk.diary.contracts.input import DiaryContract, Point, digest
from daengs_walk.diary.space.geometry import distance, feature_contains
from daengs_walk.diary.space.materials import SpaceMaterial

RULES = {
    "land_cover": {
        "scope": "feature_geometry",
        "retain": "current_point_covered_by_feature",
        "evict": "outside_land_feature",
        "update": "current_point_relation",
    },
    "park": {
        "scope": "registered_point",
        "retain": "distance_within_explicit_park_radius",
        "evict": "outside_park_distance",
        "update": "registered_point_distance",
    },
    "commerce": {
        "scope": "fixed_query_circle",
        "retain": "current_point_in_sampled_area",
        "evict": "outside_commerce_footprint",
        "update": "sample_center_offset_only",
    },
}


class SpacePolicy(DiaryContract):
    version: Literal["space-application-experiment-v1"] = "space-application-experiment-v1"
    # No implicit product distance. The replay caller must choose its experiment.
    park_radius_m: float = Field(ge=0, le=1000)

    def dictionary(self):
        return {
            "version": self.version,
            "sources": RULES,
            "parameters": {"park_radius_m": self.park_radius_m},
            "common": {
                "unlocated": "exclude_from_scene_keep_archive",
                "expiration": "explicit_batch_deadline_only",
                "conflict": "exclude_competing_claims_no_recency_winner",
                "source_failure": "no_new_material_other_materials_unchanged",
                "coexistence": "independent_applicable_materials",
                "capacity": "separate_comparison_not_memory_eviction",
            },
        }


class SpaceApplication(DiaryContract):
    material_id: str
    eligibility: Literal["pass", "fail", "unknown"]
    reason: str
    relation: dict[str, JsonValue] = Field(default_factory=dict)
    details: dict[str, JsonValue] = Field(default_factory=dict)


def claim_identity(item: SpaceMaterial):
    """Same subject/question, independent of lookup position and retrieval time.

    Area composition and distribution remain a single, multi-case assertion.
    Geometry/registered location changes are conflicts, not a distance tie-break.
    """
    scope, support = item.scope, item.support
    if item.source == "commerce":
        subject = {"point": scope["point"], "radius_m": scope["radius_m"]}
        assertion = {
            key: support[key]
            for key in (
                "classification_policy",
                "source_hash",
                "registered_count",
                "registered_sites",
                "spread_rms_m",
                "nearest_registered_point_m",
                "centroid_distance_m",
            )
        }
    elif item.source == "park":
        subject = support["source_id"]
        assertion = {"point": scope["point"], "area_m2": support["area_m2"]}
    else:
        subject = [support["layer"], support["source_id"]]
        assertion = {"crs": scope["crs"], "geometry": scope["geometry"]}
    return digest(normalize([item.source, subject])), digest(normalize([item.material, assertion]))


def apply_space(item: SpaceMaterial, point: Point | None, policy: SpacePolicy, *, coverage=None):
    def result(eligibility, reason, relation=None, **details):
        return SpaceApplication(
            material_id=item.id,
            eligibility=eligibility,
            reason=reason,
            relation=relation or {},
            details=details,
        )

    if point is None:
        return result("unknown", "scene_unlocated")
    current, scope = point.model_dump(), item.scope
    try:
        if item.source == "land_cover":
            covered = (
                coverage.covers(current)
                if coverage is not None
                else feature_contains(scope["geometry"], scope["crs"], current)
            )
            return result(
                "pass" if covered else "fail",
                "land_feature_covers_point" if covered else "outside_land_feature",
                {"kind": "land_cover_at_current_point", "distance_m": 0} if covered else {},
                boundary="covered",
            )
        if item.source == "park":
            metres = distance(Point.model_validate(scope["point"]).model_dump(), current)
            inside = metres <= policy.park_radius_m
            return result(
                "pass" if inside else "fail",
                "park_distance_applies" if inside else "outside_park_distance",
                {"kind": "registered_park_point_distance", "distance_m": metres},
                actual_m=metres,
                limit_m=policy.park_radius_m,
            )
        centre = Point.model_validate(scope["point"]).model_dump()
        radius = float(scope["radius_m"])
        if not 0 < radius <= 3000:
            raise ValueError("invalid footprint")
        offset = distance(current, centre)
        inside = offset <= radius
        return result(
            "pass" if inside else "fail",
            "sampled_area_contains_point" if inside else "outside_commerce_footprint",
            {
                "kind": "registered_distribution_in_fixed_sample_area",
                "sample_center": centre,
                "sample_radius_m": radius,
                "current_offset_from_sample_center_m": round(offset, 3),
                "current_point_in_sample_area": inside,
                # Distances in the archived normalizer are from its query, not this point.
                "sample_center_to_nearest_registration_m": item.support[
                    "nearest_registered_point_m"
                ],
                "sample_center_to_registration_centroid_m": item.support["centroid_distance_m"],
            },
            actual_m=offset,
            limit_m=radius,
            local_cluster_membership="not_determined",
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return result("unknown", "invalid_material_scope")
