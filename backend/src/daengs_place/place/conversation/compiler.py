"""Compile bounded semantic edits. The server owns IDs and untouched state."""

import hashlib
import json

from daengs_place.place.conversation.intent import SemanticChanges
from daengs_place.place.filters.contract import FilterState


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items() if k != "id"}
    if isinstance(value, list):
        return sorted((canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


def fingerprint(state: FilterState) -> str:
    return hashlib.sha256(
        json.dumps(canonical(state.model_dump(mode="json")), sort_keys=True).encode()
    ).hexdigest()


def atom(capability, value):
    return {
        "capability": capability,
        "op": "in" if capability == "purpose.kind" else "eq",
        "value": value,
    }


def compile_changes(current: FilterState, changes: SemanticChanges) -> FilterState:
    candidate = FilterState.model_validate(
        compile_filter_data(current.model_dump(mode="json"), changes)
    )
    return current if fingerprint(current) == fingerprint(candidate) else candidate


def project_branches(branches, previous_kinds, kinds):
    """Keep branch-local facts attached to their categories, never move them to new ones."""
    projected = []
    has_unscoped = False
    for branch in branches:
        scope = [a for a in branch["all"] if a["capability"] == "purpose.kind"]
        facts = [a for a in branch["all"] if a["capability"] != "purpose.kind"]
        if not scope:
            projected.append({"all": facts})
            has_unscoped = True
            continue
        allowed = [
            kind
            for kind in kinds
            if kind in previous_kinds
            and all((kind in a["value"]) == (a["op"] == "in") for a in scope)
        ]
        if allowed:
            projected.append({"all": [atom("purpose.kind", allowed), *facts]})
    added = [kind for kind in kinds if kind not in previous_kinds]
    if added and projected and not has_unscoped:
        projected.append({"all": [atom("purpose.kind", added)]})
    return projected


def compile_filter_data(current: dict, changes: SemanticChanges, *, max_kinds=6) -> dict:
    """Shared facet algebra; each search capability validates its own final envelope."""
    if (
        current["hard"]["any"]
        and changes.alternatives is not None
        and (changes.parking != "keep" or changes.exclusive != "keep")
        and changes.kinds is None
    ):
        # A facet edit and complete expression replacement have different authority.
        # Reject ambiguous double writes instead of erasing unrelated branch-local facts.
        raise ValueError("facet edit and OR replacement must be requested separately")
    data = canonical(current)
    # Keep candidate/display order as selected by the user, not canonical sort order.
    kinds = list(current["candidate_kinds"])
    if edit := changes.kinds:
        requested = list(dict.fromkeys(edit.values))
        if edit.operation == "set":
            kinds = requested
        elif edit.operation == "add":
            kinds = list(dict.fromkeys([*kinds, *requested]))
        else:
            kinds = [kind for kind in kinds if kind not in requested]
    if not kinds or len(kinds) > max_kinds:
        raise ValueError("invalid category scope")
    data["candidate_kinds"] = kinds
    hard = data["hard"]
    if set(kinds) != set(current["candidate_kinds"]):
        hard["all"] = [a for a in hard["all"] if a["capability"] != "purpose.kind"]
        if hard["any"] and changes.alternatives is None:
            hard["any"] = project_branches(hard["any"], current["candidate_kinds"], kinds)
        preferences = []
        for preference in data["preferences"]:
            scope = preference["scope_kinds"]
            scope = (
                kinds
                if set(scope) == set(current["candidate_kinds"])
                else [k for k in scope if k in kinds]
            )
            if scope:
                preferences.append({**preference, "scope_kinds": scope})
        data["preferences"] = preferences

    for capability, mode in (
        ("operations.parking", changes.parking),
        ("pet_access.exclusive", changes.exclusive),
    ):
        if mode == "keep":
            continue
        hard["all"] = [a for a in hard["all"] if a["capability"] != capability]
        branches = [
            {"all": [a for a in b["all"] if a["capability"] != capability]} for b in hard["any"]
        ]
        # Removing the only condition from an OR branch makes the disjunction true.
        hard["any"] = [] if any(not b["all"] for b in branches) else branches
        data["preferences"] = [p for p in data["preferences"] if p["capability"] != capability]
        if mode.startswith("required_"):
            hard["all"].append(atom(capability, mode == "required_true"))
        elif mode == "preferred_true":
            data["preferences"].append({**atom(capability, True), "scope_kinds": kinds})

    if changes.alternatives is not None:
        hard["any"] = []
        for alternative in changes.alternatives:
            atoms = []
            if alternative.kinds is not None:
                atoms.append(atom("purpose.kind", list(alternative.kinds)))
            for field, capability in (
                ("parking", "operations.parking"),
                ("exclusive", "pet_access.exclusive"),
            ):
                if (value := getattr(alternative, field)) is not None:
                    atoms.append(atom(capability, value))
            hard["any"].append({"all": atoms})
    if changes.name_query is not None:
        data["name_query"] = changes.name_query
    if changes.radius_m is not None:
        data["spatial"]["radius_m"] = changes.radius_m

    def unique(items):
        by_value = {json.dumps(canonical(item), sort_keys=True): item for item in items}
        return [by_value[key] for key in sorted(by_value)]

    hard["all"] = unique(hard["all"])
    for branch in hard["any"]:
        branch["all"] = unique(branch["all"])
    hard["any"] = unique(hard["any"])
    data["preferences"] = unique(data["preferences"])

    # Stable, context-qualified IDs: duplicate atoms in different branches never collide.
    def identify(item, context):
        digest = hashlib.sha256(json.dumps(canonical(item), sort_keys=True).encode()).hexdigest()[
            :16
        ]
        return {"id": f"{context}-{digest}", **item}

    hard["all"] = [identify(a, "all") for a in hard["all"]]
    hard["any"] = [
        {**identify(b, "or"), "all": [identify(a, f"or-{i}") for a in b["all"]]}
        for i, b in enumerate(hard["any"])
    ]
    data["preferences"] = [identify(p, "prefer") for p in data["preferences"]]
    # Preserve dog snapshots byte-for-byte, including their order.
    data["dogs"] = current["dogs"]
    return data
