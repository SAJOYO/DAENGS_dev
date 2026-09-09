"""Records-first scene centres, bounded background slots, and reproducible stamps.

Ports Geo scene_core.choose_supplements and the walk-input-v1 background boundary.
Receives already verified movement observations; it does not calculate route HOW.
"""

from datetime import timedelta
from typing import Literal

from pydantic import Field, model_validator

from daengs_walk.diary_background import piece_identity, piece_rank, project_background
from daengs_walk.diary_input import DiaryContract, DiaryInput, MaterialRef, digest, material_ref
from daengs_walk.diary_output import DiaryPlan, SceneStamp


class StampPolicy(DiaryContract):
    version: Literal["records-first-v1"] = "records-first-v1"
    target_scene_count: int = Field(ge=1, le=50)  # Explicit caller choice, no product default.
    separation_s: float = Field(default=20, ge=0, le=3600)
    place_slots: int = Field(default=1, ge=0, le=4)
    space_slots: int = Field(default=3, ge=0, le=8)
    environment_slots: int = Field(default=1, ge=0, le=4)
    time_slots: int = Field(default=1, ge=0, le=4)

    @model_validator(mode="after")
    def capacity(self):
        if sum(self.capacities().values()) > 16:
            raise ValueError("background slots exceed the stamp contract")
        return self

    def capacities(self):
        return {
            "place_reference": self.place_slots,
            "space_relation": self.space_slots,
            "environment": self.environment_slots,
            "time": self.time_slots,
        }


class CoreDecision(DiaryContract):
    material: MaterialRef
    reason: Literal[
        "user_record",
        "deleted_record",
        "user_target_met",
        "near_user_record_time",
        "overlapping_selected_observation",
        "deficit_filled",
        "fill_scene_deficit",
    ]


class BackgroundDecision(DiaryContract):
    background_id: str
    reason: str
    piece_id: str | None = None
    rejected_rows: tuple[int, ...] = ()


class PreparedDiary(DiaryContract):
    format: Literal["walk-diary-prepared-v1"] = "walk-diary-prepared-v1"
    policy: StampPolicy
    plan: DiaryPlan
    core_decisions: tuple[CoreDecision, ...]
    background_decisions: tuple[BackgroundDecision, ...]
    counts: dict[str, int]
    limits: tuple[str, ...]

    def validate_against(self, source: DiaryInput):
        """Replay the domain decision, including the projected facts; no model/verifier call."""
        if self != prepare_stamps(source, self.policy):
            raise ValueError("prepared diary differs from the current source/policy")


def _centres(source, policy):
    records = sorted(
        (r for r in source.records if not r.deleted),
        key=lambda r: (r.anchor.event_at, r.ref.identity),
    )
    decisions = [
        CoreDecision(
            material=material_ref(r), reason="deleted_record" if r.deleted else "user_record"
        )
        for r in sorted(source.records, key=lambda r: r.ref.identity)
    ]
    deficit = max(0, policy.target_scene_count - len(records))
    margin = timedelta(seconds=policy.separation_s)
    selected = []
    for candidate in sorted(
        source.observations,
        key=lambda o: (
            o.kind != "observed_dwell",
            -(o.ended_at - o.started_at).total_seconds(),
            o.anchor.event_at,
            o.id,
        ),
    ):
        if not deficit:
            reason = "user_target_met"
        elif any(
            candidate.started_at - margin <= r.anchor.event_at <= candidate.ended_at + margin
            for r in records
            if r.anchor.time_basis != "session_fallback"
        ):
            reason = "near_user_record_time"  # Editing proximity, never inferred co-location.
        elif any(
            candidate.started_at <= other.ended_at + margin
            and candidate.ended_at >= other.started_at - margin
            for other in selected
        ):
            reason = "overlapping_selected_observation"
        elif len(selected) >= deficit:
            reason = "deficit_filled"
        else:
            selected.append(candidate)
            reason = "fill_scene_deficit"
        decisions.append(CoreDecision(material=material_ref(candidate), reason=reason))
    centres = sorted(
        (*records, *selected), key=lambda m: (m.anchor.event_at, material_ref(m).identity)
    )
    return (
        centres,
        decisions,
        {
            "target": policy.target_scene_count,
            "user_records": len(records),
            "initial_deficit": deficit,
            "supplemented": len(selected),
            "remaining_deficit": deficit - len(selected),
            "total": len(centres),
        },
    )


def _backgrounds(source, centres, policy):
    """Slots reset for each exact core. Prior scene context cannot become current context."""
    by_core = {material_ref(m).identity: m for m in centres}
    candidates = {identity: [] for identity in by_core}
    saved_by_id = {b.id: b for b in source.backgrounds}
    admitted = set(source.selected_background_ids)
    decisions = []
    for saved in sorted(source.backgrounds, key=lambda b: b.id):
        core = by_core.get(saved.target.identity)
        reason = None
        if core is None:
            reason = "core_not_selected"
        elif saved.status not in {"known", "partial"}:
            reason = "source_" + saved.status
        elif saved.id not in admitted:
            reason = "not_selected_input"
        else:
            projection = project_background(saved, core)
            if projection.rejected_rows:
                decisions.append(
                    BackgroundDecision(
                        background_id=saved.id,
                        reason="invalid_provider_rows",
                        rejected_rows=projection.rejected_rows,
                    )
                )
            reason = projection.reason
            candidates[saved.target.identity].extend(projection.pieces)
        if reason:
            decisions.append(BackgroundDecision(background_id=saved.id, reason=reason))
    result = {}
    for identity, pool in candidates.items():
        slots = {kind: [] for kind in policy.capacities()}
        seen = set()
        for piece in sorted(pool, key=lambda p: piece_rank(p, saved_by_id[p.background_id])):
            key = piece_identity(piece)
            if key in seen:
                reason = "duplicate_place_reference"
            elif len(slots[piece.kind]) >= policy.capacities()[piece.kind]:
                reason = "slot_capacity"
            else:
                seen.add(key)
                slots[piece.kind].append(piece)
                reason = "admit"
            decisions.append(
                BackgroundDecision(
                    background_id=piece.background_id,
                    piece_id=piece.id,
                    reason=reason,
                )
            )
        result[identity] = tuple(p for pieces in slots.values() for p in pieces)
    return result, decisions


def prepare_stamps(source: DiaryInput, policy: StampPolicy) -> PreparedDiary:
    # Frozen models can still contain mutable dicts. Copy/revalidate before selecting or hashing.
    source = DiaryInput.model_validate(source.model_dump(mode="json"))
    policy = StampPolicy.model_validate(policy.model_dump(mode="json"))
    if source.scene_policy_version != policy.version:
        raise ValueError("unsupported scene selection policy")
    if source.photos_status == "pending":
        raise ValueError("photo snapshot is still changing")
    centres, core_decisions, counts = _centres(source, policy)
    backgrounds, background_decisions = _backgrounds(source, centres, policy)
    plan = DiaryPlan(
        input_revision=source.revision(),
        target_scene_count=policy.target_scene_count,
        preparation_policy=policy.model_dump(mode="json"),
        scenes=tuple(
            SceneStamp(
                id="stamp:" + digest(material_ref(m).identity),
                core=material_ref(m),
                background=backgrounds[material_ref(m).identity],
            )
            for m in centres
        ),
    )
    plan.validate_against(source)
    limits = []
    if source.route.status != "ready":
        limits.append("route_unavailable")
    if not source.observations:
        limits.append("observation_pool_empty")
    if source.photos_status == "not_available":
        limits.append("photo_metadata_not_available")
    return PreparedDiary(
        policy=policy,
        plan=plan,
        core_decisions=tuple(core_decisions),
        background_decisions=tuple(background_decisions),
        counts=counts,
        limits=tuple(limits),
    )
