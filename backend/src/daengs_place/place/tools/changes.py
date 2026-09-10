"""Apply typed edits to the common filter state without I/O."""

from daengs_place.place.filters.contract import FilterState, guard_filter_state
from daengs_place.place.tools.contract import FilterChanges


def _edit_items(current, upserts, removes):
    items = {item["id"]: item for item in current}
    if set(removes) - items.keys():
        raise ValueError("unknown_filter_id")
    for item_id in removes:
        del items[item_id]
    for item in upserts:
        items[item.id] = item.model_dump(mode="json")
    return list(items.values())


def apply_changes(state: FilterState, changes: FilterChanges) -> FilterState:
    data = guard_filter_state(state).model_dump(mode="json")
    for field in ("candidate_kinds", "name_query"):
        if field in changes.model_fields_set:
            data[field] = getattr(changes, field)
    if changes.radius_m is not None:
        data["spatial"]["radius_m"] = changes.radius_m
    for group in ("all", "any", "preferences"):
        parent = data if group == "preferences" else data["hard"]
        parent[group] = _edit_items(
            parent[group], getattr(changes, f"upsert_{group}"), getattr(changes, f"remove_{group}")
        )
    return FilterState.model_validate(data)
