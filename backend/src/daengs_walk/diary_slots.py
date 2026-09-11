"""Exclusion-first, per-scene part slots. No provider calls, prose or persistent cache."""

from typing import Literal

from pydantic import Field, JsonValue

from daengs_walk.diary_board import BaseBoard, BaseBoardPolicy, BoardScene
from daengs_walk.diary_board_assembly import assemble_base_board
from daengs_walk.diary_board_selection import prepare_base_board
from daengs_walk.diary_input import DiaryContract, Digest, Identifier, digest

Part = Literal["space", "environment", "motion"]


class SlotPolicy(DiaryContract):
    version: Literal["diary-part-slots-v2"] = "diary-part-slots-v2"
    space_slots: int = Field(default=3, ge=0, le=8)
    environment_slots: int = Field(default=1, ge=0, le=4)
    motion_slots: int = Field(default=1, ge=0, le=4)
    total_slots: int = Field(default=8, ge=0, le=16)
    space_radius_m: float = Field(default=250, ge=0, le=1000)
    motion_gap_s: float = Field(default=30, ge=0, le=120)
    route_tolerance_m: float = Field(default=30, ge=0, le=100)
    location_age_s: float = Field(default=30, ge=0, le=120)
    include_location_reference: bool = True

    def capacity(self, part: Part):
        return getattr(self, part + "_slots")


class SlotSource(DiaryContract):
    source_id: Identifier
    source_version: Digest


class SlotEvidence(DiaryContract):
    id: Identifier
    part: Part
    role: Identifier
    source_id: Identifier
    source_version: Digest
    entity_key: Digest
    claim_scope: Digest
    claim_key: Digest
    sources: tuple[SlotSource, ...]
    facts: dict[str, JsonValue]
    diagnostics: dict[str, JsonValue] = Field(default_factory=dict)
    # Rank only resolves an overflowing part. It never decides applicability.
    rank: tuple[float, ...]


class SlotDecision(DiaryContract):
    source_id: str
    evidence_id: str | None = None
    part: Part
    eligibility: Literal["pass", "fail", "unknown"]
    admission: Literal[
        "kept", "excluded", "duplicate", "conflict", "part_capacity", "total_capacity"
    ]
    reason: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class PartStamp(DiaryContract):
    scene_id: Identifier
    evidence: tuple[SlotEvidence, ...] = Field(max_length=16)
    decisions: tuple[SlotDecision, ...]
    location_reference: SlotEvidence | None = None

    def materials(self):
        return self.evidence + ((self.location_reference,) if self.location_reference else ())


class SlotPreview(DiaryContract):
    format: Literal["walk-diary-slots-preview-v1"] = "walk-diary-slots-preview-v1"
    policy: SlotPolicy
    revision: Digest
    base_board: BaseBoard
    stamps: tuple[PartStamp, ...]
    scenes: tuple[BoardScene, ...]
    model_status: Literal["not_requested", "accepted", "unavailable"] = "not_requested"
    failure_code: Literal["provider_failed", "invalid_response", "budget_exceeded"] | None = None
    citations: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    writing_revision: Digest | None = None


def admit(scene_id, candidates, decisions, policy):
    """Rebuild slots from applicable materials; fewer materials is a valid result.

    Total capacity is round-robin over each part's ranked queue (space/env/motion).
    This is a visible budget tie-break, not a required mix or cross-part score.
    """
    from daengs_walk.diary_slot_claims import resolve_claims, spatial_order

    candidates = resolve_claims(candidates, decisions)
    applicable = []
    for item in candidates:
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
    candidates = applicable
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
        decisions=tuple(decisions),
    )


def prepare_slot_preview(source, policy: SlotPolicy, board_policy: BaseBoardPolicy, *, route=None):
    from daengs_walk.diary_slot_sources import candidates_for_scene, verified_motion

    plan = prepare_base_board(source, board_policy, route=route)
    board = assemble_base_board(source, plan, route=route)
    motion, blocks = verified_motion(source, route)
    stamps = []
    for scene in board.scenes:
        candidates, decisions = candidates_for_scene(source, scene, policy, motion, blocks)
        stamps.append(admit(scene.id, candidates, decisions, policy))
    revision = digest(
        {
            "board": board.plan_revision,
            "policy": policy.model_dump(mode="json"),
            "stamps": [s.model_dump(mode="json") for s in stamps],
        }
    )
    return SlotPreview(
        policy=policy,
        revision=revision,
        base_board=board,
        stamps=tuple(stamps),
        scenes=board.scenes,
    )
