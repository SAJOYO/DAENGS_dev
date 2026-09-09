"""Action content and its independent location resolution. Never an attestation."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from daengs_backend.schemas.walk_entry import EntryContent, EntryLocation, RecordProfileResponse


def utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("timezone is required")
    return value.astimezone(UTC)


class LocationV2(EntryLocation):
    _utc = field_validator("captured_at")(utc)


class ContentV2(EntryContent):
    location: LocationV2 | None = None
    _utc = field_validator("recorded_at")(utc)

    @model_validator(mode="after")
    def valid_content(self):
        if self.kind == "behavior":
            if self.behavior_code is None or self.note is not None:
                raise ValueError("behavior requires a code and no note")
        else:
            if self.behavior_code is not None or not self.note or not self.note.strip():
                raise ValueError("note requires text and no behavior code")
            if self.pet_id is not None:
                raise ValueError("note belongs to the whole walk")
            self.note = self.note.strip()
        return self


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PinPoint(StrictModel):
    lat: float = Field(ge=-90, le=90, strict=True)
    lng: float = Field(ge=-180, le=180, strict=True)


class SourceRef(StrictModel):
    client_seq: int = Field(ge=0, strict=True)
    chain_index: int = Field(ge=0, strict=True)
    at: datetime
    _utc = field_validator("at")(utc)


class Pin(StrictModel):
    resolution_id: uuid.UUID
    state: Literal["provisional", "resolved", "unlocated"]
    method: Literal["observed", "estimated", "last_known", "none"]
    target_at: datetime
    point: PinPoint | None
    computed_at: datetime
    policy_version: str = Field(min_length=1, max_length=100)
    algorithm_version: str = Field(min_length=1, max_length=100)
    resolve_by: datetime
    source_refs: list[SourceRef] = Field(max_length=256)
    uncertainty_m: float | None = Field(gt=0, strict=True)
    uncertainty_basis: Literal["provider_accuracy", "model_bound", "unknown"]
    reason: Literal[
        "direct_fix",
        "awaiting_observations",
        "refined",
        "deadline",
        "session_ended",
        "recovered",
        "estimator_failed",
        "no_evidence",
    ]
    _utc = field_validator("target_at", "computed_at", "resolve_by")(utc)

    @model_validator(mode="after")
    def invariants(self):
        if (self.state == "provisional") != (self.reason == "awaiting_observations"):
            raise ValueError("awaiting_observations is only the provisional reason")
        if (self.method == "observed") != (self.reason == "direct_fix"):
            raise ValueError("direct_fix is only the observed reason")
        if self.computed_at < self.target_at or self.resolve_by < self.target_at:
            raise ValueError("calculation/deadline cannot precede target")
        if (self.uncertainty_basis == "unknown") != (self.uncertainty_m is None):
            raise ValueError("unknown uncertainty must be null; other bases need a positive radius")
        if self.uncertainty_basis == "provider_accuracy" and self.method != "observed":
            raise ValueError("provider accuracy cannot certify an estimate")
        if self.state == "provisional" and self.method not in {"estimated", "last_known", "none"}:
            raise ValueError("invalid provisional method")
        if self.state == "resolved" and self.point is None:
            raise ValueError("resolved requires a coordinate")
        if self.state == "unlocated" and self.point is not None:
            raise ValueError("unlocated has no coordinate")
        if (self.point is None) != (self.method == "none"):
            raise ValueError("point and method disagree")
        if (self.point is None) != (not self.source_refs):
            raise ValueError("coordinates need source references")
        keys = [(ref.at, ref.client_seq) for ref in self.source_refs]
        if keys != sorted(keys) or len({r.client_seq for r in self.source_refs}) != len(keys):
            raise ValueError("references must be sorted and unique")
        if any(r.at > min(self.resolve_by, self.computed_at) for r in self.source_refs):
            raise ValueError("reference exceeds available observation window")
        if self.state == "provisional" and any(r.at > self.target_at for r in self.source_refs):
            raise ValueError("initial pin cannot use post-tap observations")
        if self.method in {"observed", "last_known"} and (
            len(self.source_refs) != 1 or self.source_refs[0].at > self.target_at
        ):
            raise ValueError("copied point requires exactly one past observation")
        if self.method == "estimated" and len({r.chain_index for r in self.source_refs}) > 1:
            raise ValueError("estimation cannot cross pause chains")
        return self


class EntryWriteV2(StrictModel):
    expected_revision: int = Field(ge=0, strict=True)
    mutation_id: uuid.UUID
    content: ContentV2
    # Omission means content update. Explicit null is the note creation form.
    pin: Pin | None = None


class PinWrite(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    expected_pin_revision: int = Field(ge=0, strict=True)
    mutation_id: uuid.UUID
    pin: Pin


class Tombstone(StrictModel):
    id: uuid.UUID
    revision: int
    mutation_id: uuid.UUID
    deleted: Literal[True] = True


class EntryResponseV2(StrictModel):
    contract_version: Literal["walk-entry-v2"] = "walk-entry-v2"
    id: uuid.UUID
    revision: int
    pin_revision: int
    mutation_id: uuid.UUID
    deleted: Literal[False] = False
    content: ContentV2
    # Legacy pins intentionally have no manufactured source references. Constructed server-side only.
    pin: dict | None


class EntryListV2(StrictModel):
    contract_version: Literal["walk-entry-v2"] = "walk-entry-v2"
    revision: str
    entries: list[EntryResponseV2 | Tombstone]


class EvidenceV2(ContentV2):
    entry_id: uuid.UUID
    entry_revision: int
    walk_id: uuid.UUID
    context_status: Literal["not_requested"] = "not_requested"
    context_refs: list[str] = Field(default_factory=list)
    pin: dict | None
    pin_revision: int


class ProfileV2(RecordProfileResponse):
    contract_version: Literal["walk-entry-v2"] = "walk-entry-v2"
    evidence: list[EvidenceV2]
