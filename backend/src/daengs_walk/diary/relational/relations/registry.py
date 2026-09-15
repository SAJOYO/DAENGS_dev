"""Fixed ordered inventory. Adding a family requires an explicit schema slot."""

from . import (
    area_context,
    background,
    continuity,
    event_context,
    movement,
    point_context,
    proximity,
    route_revisit,
)
from .contracts import RelationSlots

MODULES = (background, proximity, continuity, route_revisit, movement, event_context)

# New snapshot contract; legacy v6 inventory stays readable until writer migration.
COMPARISON_MODULES = {
    "background": point_context,
    "proximity": proximity,
    "area_context": area_context,
}


def collect_relations(frame, previous):
    results = {m.__name__.rsplit(".", 1)[-1]: m.evaluate(frame, previous) for m in MODULES}
    return RelationSlots.model_validate(results).model_dump(mode="json")


def collect_spatial_comparisons(current, earlier=None):
    from daengs_walk.diary.relational.scene_comparison_contracts import (
        SceneSnapshot,
        SpatialComparisonSlots,
    )

    current = SceneSnapshot.model_validate(current)
    earlier = SceneSnapshot.model_validate(earlier) if earlier is not None else None
    if earlier is not None:
        if current.walk_id != earlier.walk_id or current.scene_id == earlier.scene_id:
            raise ValueError("comparison requires distinct scenes in the same walk")
        if current.recorded_at < earlier.recorded_at:
            raise ValueError("comparison requires chronological scenes")
        if {f.id for f in current.facts} & {f.id for f in earlier.facts}:
            raise ValueError("comparison evidence IDs must be scene-qualified")
    return SpatialComparisonSlots.model_validate(
        {
            name: module.evaluate_snapshot(current, earlier)
            for name, module in COMPARISON_MODULES.items()
        }
    ).model_dump(mode="json")
