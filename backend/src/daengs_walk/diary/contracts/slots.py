"""Part budgets and evidence snapshots, independent of board selection."""

from typing import Literal

from pydantic import Field, JsonValue

from daengs_walk.diary.contracts.input import DiaryContract, Digest, Identifier, digest
from daengs_walk.diary.route.patterns import RoutePatternBindingPolicy

Part = Literal["space", "environment", "motion"]


class SlotPolicy(DiaryContract):
    version: Literal["diary-part-slots-v3"] = "diary-part-slots-v3"
    space_slots: int = Field(default=3, ge=0, le=8)
    environment_slots: int = Field(default=1, ge=0, le=4)
    motion_slots: int = Field(default=1, ge=0, le=4)
    total_slots: int = Field(default=8, ge=0, le=16)
    space_radius_m: float = Field(default=250, ge=0, le=1000)
    motion_gap_s: float = Field(default=30, ge=0, le=120)
    route_tolerance_m: float = Field(default=30, ge=0, le=100)
    location_age_s: float = Field(default=30, ge=0, le=120)
    weather_max_age_s: float = Field(default=7200, ge=0, le=7200)
    include_location_reference: bool = True
    route_patterns: RoutePatternBindingPolicy | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

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


class BoardSlotSnapshot(DiaryContract):
    """Private evidence for one selected board; no prose, persistence or UI contract."""

    client_session_id: Identifier
    input_revision: Digest
    plan_revision: Digest
    policy: SlotPolicy
    stamps: tuple[PartStamp, ...]

    def revision(self):
        # The board plan already binds the source revision. Keep the preview-v1
        # digest stable so the same board/policy/materials have one identity.
        return digest(
            {
                "board": self.plan_revision,
                "policy": self.policy.model_dump(mode="json"),
                "stamps": [s.model_dump(mode="json") for s in self.stamps],
            }
        )
