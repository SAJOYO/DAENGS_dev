"""Resolve claims and apply per-part and total capacity to eligible materials."""

from daengs_walk.diary.contracts.slots import PartStamp, SlotDecision


def eligible_candidates(candidates, decisions, policy):
    """Resolve meaning and scope before any writing capacity is applied."""
    from daengs_walk.diary.slots.claims import SPATIAL_ORDER, resolve_claims

    candidates = resolve_claims(candidates, decisions)
    applicable = []
    for item in candidates:
        if item.part == "space" and item.role not in (*SPATIAL_ORDER, "scene_address_reference"):
            decisions.append(
                SlotDecision(
                    source_id=item.source_id,
                    evidence_id=item.id,
                    part="space",
                    eligibility="unknown",
                    admission="excluded",
                    reason="unsupported_spatial_relation",
                    details={"role": item.role},
                )
            )
            continue
        # Resolve incompatible claims first, even if one of their distances is
        # outside the requested radius. Filtering it first could hide a conflict.
        if item.role in {"scene_registered_point_distance", "scene_geometry_distance"} and (
            item.facts["distance_m"] > policy.space_radius_m
        ):
            decisions.append(
                SlotDecision(
                    source_id=item.source_id,
                    evidence_id=item.id,
                    part="space",
                    eligibility="fail",
                    admission="excluded",
                    reason="outside_space_radius",
                    details=item.diagnostics,
                )
            )
        else:
            applicable.append(item)
    return applicable


def admit(scene_id, candidates, decisions, policy, *, eligible_out=None):
    """Apply the existing budgets; optionally retain the complete eligible frame."""
    from daengs_walk.diary.slots.claims import spatial_order

    candidates = eligible_candidates(candidates, decisions, policy)
    if eligible_out is not None:
        eligible_out.extend(candidates)
    # Display metadata is independent of prose capacity. Conflicts were resolved above.
    temperatures = sorted(
        (c for c in candidates if c.role == "grid_temperature_observation"),
        key=lambda c: (c.rank, c.id),
    )
    temperature = temperatures[0] if temperatures else None
    addresses = sorted(
        (c for c in candidates if c.role == "scene_address_reference"), key=lambda c: c.id
    )
    location = addresses[0] if addresses and policy.include_location_reference else None
    for item in addresses:
        decisions.append(
            SlotDecision(
                source_id=item.source_id,
                evidence_id=item.id,
                part="space",
                eligibility="pass",
                admission="kept" if item == location else "excluded",
                reason="location_reference" if item == location else "location_reference_budget",
                details={"actual": len(addresses), "limit": int(policy.include_location_reference)},
            )
        )
    candidates = [c for c in candidates if c.role != "scene_address_reference"]
    queues = {part: [] for part in ("space", "environment", "motion")}
    pending = []
    for part, queue in queues.items():
        items = [c for c in candidates if c.part == part]
        ordered_part = (
            spatial_order(items) if part == "space" else sorted(items, key=lambda c: (c.rank, c.id))
        )
        for item in ordered_part:
            reason = "applicable"
            admission = "kept"
            if len(queue) >= policy.capacity(part):
                admission, reason = "part_capacity", "part_budget"
            else:
                queue.append(item)
            pending.append((item, admission, reason))
    ordered = [queue[i] for i in range(8) for queue in queues.values() if i < len(queue)]
    if policy.movement is not None:
        ordered = queues["motion"] + [c for c in ordered if c.part != "motion"]
    kept = ordered[: policy.total_slots]
    kept_ids = {item.id for item in kept}
    for item, admission, reason in pending:
        if admission == "kept" and item.id not in kept_ids:
            admission, reason = "total_capacity", "stamp_budget"
        decisions.append(
            SlotDecision(
                source_id=item.source_id,
                evidence_id=item.id,
                part=item.part,
                eligibility="pass",
                admission=admission,
                reason=reason,
                details={
                    **item.diagnostics,
                    "part_limit": policy.capacity(item.part),
                    "total_limit": policy.total_slots,
                },
            )
        )
    return PartStamp(
        scene_id=scene_id,
        evidence=tuple(kept),
        location_reference=location,
        temperature_reference=temperature,
        decisions=tuple(decisions),
    )
