"""Private, versioned inputs for the diary domain. No routing, DB or provider calls.

Raw GPS is resolved by the walk service using RouteVersion, not placed in assistant
graph state. References bind already-computed movement and saved context to this
snapshot. These contracts do not implement their acquisition or scene selection.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, ConfigDict, Field, JsonValue, model_validator

from daengs_walk.contracts import FrozenContract

Instant = Annotated[AwareDatetime, AfterValidator(lambda at: at.astimezone(UTC))]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]


class DiaryContract(FrozenContract):
    # The shared assistant ContractModel strips strings. User notes must not use it.
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def digest(value) -> str:
    if isinstance(value, DiaryContract):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class RecordRef(DiaryContract):
    store: Literal["walk_entry", "walk_photo"]
    id: Identifier
    version: Identifier
    version_kind: Literal["revision", "sha256"]
    pin_revision: int | None = Field(default=None, ge=0)

    @property
    def identity(self):
        return f"{self.store}:{self.id}"

    @model_validator(mode="after")
    def version_shape(self):
        if self.version_kind == "revision" and (
            not self.version.isascii()
            or not self.version.isdigit()
            or int(self.version) < 1
            or str(int(self.version)) != self.version
        ):
            raise ValueError("a stored revision is a canonical positive integer")
        if self.version_kind == "sha256" and (
            len(self.version) != 64 or any(c not in "0123456789abcdef" for c in self.version)
        ):
            raise ValueError("metadata version must be a SHA256 digest")
        return self


class Point(DiaryContract):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class FixRef(DiaryContract):
    client_seq: int = Field(ge=0)
    chain_index: int = Field(ge=0)
    at: Instant


class Anchor(DiaryContract):
    event_at: Instant
    time_basis: Literal["recorded_at", "photo_capture", "session_fallback", "route_observation"]
    point: Point | None
    location_at: Instant | None
    accuracy_m: float | None = Field(default=None, ge=0)
    position_state: Literal["legacy", "provisional", "resolved", "unlocated"]
    method: Literal["observed", "estimated", "last_known", "none"]
    source_fixes: tuple[FixRef, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def position(self):
        if (self.point is None) != (self.method == "none"):
            raise ValueError("unlocated anchors have no spatial method")
        if self.position_state == "unlocated" and self.point is not None:
            raise ValueError("unlocated anchor cannot carry a point")
        if self.position_state in {"resolved", "legacy"} and self.point is None:
            raise ValueError("located anchor requires a point")
        if self.point is None and (self.location_at is not None or self.source_fixes):
            raise ValueError("unlocated anchor cannot carry observed location support")
        if self.method in {"observed", "last_known"} and self.location_at is None:
            raise ValueError("observed/copied position keeps its own sample time")
        if self.method in {"observed", "last_known"} and self.location_at > self.event_at:
            raise ValueError("observed/copied position cannot come from the future")
        if self.time_basis == "session_fallback" and self.point is not None:
            raise ValueError("session-wide record has no invented event location")
        if len({r.client_seq for r in self.source_fixes}) != len(self.source_fixes):
            raise ValueError("duplicate source fix")
        if self.method == "estimated" and len({r.chain_index for r in self.source_fixes}) > 1:
            raise ValueError("estimated position cannot cross a pause chain")
        return self


class Behavior(DiaryContract):
    kind: Literal["behavior"]
    code: Literal["sniffing", "excretion", "barking"]
    pet_id: Identifier | None = None


class Note(DiaryContract):
    kind: Literal["note"]
    text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.text.strip():
            raise ValueError("blank note")
        return self


class Photo(DiaryContract):
    kind: Literal["photo"]
    media_ref: Identifier


RecordContent = Annotated[Behavior | Note | Photo, Field(discriminator="kind")]


class UserRecord(DiaryContract):
    ref: RecordRef
    deleted: bool = False
    content: RecordContent | None
    anchor: Anchor | None
    # Original v2 resolution metadata stays private; no loss of uncertainty/policy history.
    pin_payload: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def original(self):
        if self.deleted:
            if self.content is not None or self.anchor is not None or self.pin_payload is not None:
                raise ValueError("tombstone carries no live record or position")
        else:
            if self.content is None or self.anchor is None:
                raise ValueError("live record requires content and its anchor")
            photo = isinstance(self.content, Photo)
            if photo != (self.ref.store == "walk_photo"):
                raise ValueError("photo/entry store mismatch")
            if photo != (self.anchor.time_basis == "photo_capture"):
                raise ValueError("photo retains capture-time semantics")
        return self


class RouteVersion(DiaryContract):
    status: Literal["ready", "unavailable"]
    analysis_id: Identifier | None
    input_fingerprint: Digest | None
    calculation_version: int | None = Field(ge=1)
    reason: Identifier | None = None

    @model_validator(mode="after")
    def ready_source(self):
        if self.status == "ready" and (
            self.analysis_id is None
            or self.input_fingerprint is None
            or self.calculation_version is None
            or self.reason is not None
        ):
            raise ValueError("ready route requires a versioned finalized analysis")
        if self.status == "unavailable" and not self.reason:
            raise ValueError("unavailable route requires a reason")
        return self


class MovementObservation(DiaryContract):
    id: Identifier
    version: Digest
    analysis_id: Identifier
    kind: Literal["observed_dwell", "observed_fast", "observed_slow"]
    subject: Literal["recording_device"] = "recording_device"
    action_meaning: Literal["not_inferred"] = "not_inferred"
    started_at: Instant
    ended_at: Instant
    anchor: Anchor

    @model_validator(mode="after")
    def support(self):
        if not self.started_at <= self.anchor.event_at <= self.ended_at:
            raise ValueError("observation anchor must lie within its own support")
        if (
            self.anchor.time_basis != "route_observation"
            or self.anchor.point is None
            or self.anchor.method != "observed"
            or self.anchor.position_state != "resolved"
            or self.anchor.location_at != self.anchor.event_at
            or not self.anchor.source_fixes
        ):
            raise ValueError("movement anchor must be an observed route point")
        if not any(f.at == self.anchor.event_at for f in self.anchor.source_fixes):
            raise ValueError("movement anchor requires a matching source fix")
        return self


class MaterialRef(DiaryContract):
    # Record identity includes the original store. A photo and an entry may share an ID.
    identity: str = Field(min_length=1, max_length=220)
    version: Digest


def material_ref(material: UserRecord | MovementObservation):
    identity = (
        material.ref.identity if isinstance(material, UserRecord) else f"observation:{material.id}"
    )
    return MaterialRef(identity=identity, version=digest(material))


class SavedBackground(DiaryContract):
    id: Identifier
    target: MaterialRef
    # A comparison also depends on the previous record/pin, not only this one.
    supporting_targets: tuple[MaterialRef, ...] = Field(default=(), max_length=16)
    provider: Identifier
    payload_schema: Identifier
    policy_version: Identifier
    query_point: Point | None
    tags: tuple[Literal["space", "environment", "time"], ...] = Field(min_length=1)
    status: Literal["known", "partial", "empty", "unavailable", "not_requested"]
    reason: Identifier | None = None
    retrieved_at: Instant | None
    temporal_basis: Literal["lookup_snapshot", "event_observation", "source_observation", "unknown"]
    valid_from: Instant | None = None
    valid_until: Instant | None = None
    payload: dict[str, JsonValue] | None
    payload_sha256: Digest | None

    @model_validator(mode="after")
    def integrity(self):
        if self.status in {"known", "partial", "empty"}:
            if (
                self.payload is None
                or self.payload_sha256 != digest(self.payload)
                or self.retrieved_at is None
            ):
                raise ValueError(
                    "saved background requires its original payload hash and query time"
                )
        elif self.payload is not None or self.payload_sha256 is not None:
            raise ValueError("missing result cannot contain successful data")
        if self.status in {"partial", "unavailable", "not_requested"} and self.reason is None:
            raise ValueError("missing/partial background requires a reason")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("duplicate background tag")
        if (self.valid_from is None) != (self.valid_until is None):
            raise ValueError("temporal support requires both boundaries")
        if self.valid_from is not None and self.valid_from > self.valid_until:
            raise ValueError("invalid temporal support")
        if (
            self.temporal_basis == "event_observation"
            and self.status in {"known", "partial"}
            and self.valid_from is None
        ):
            raise ValueError("event-time context requires a supported time range")
        return self


class PhotoManifestRef(DiaryContract):
    publisher_id: Identifier
    revision: int = Field(ge=1)


class DiaryInput(DiaryContract):
    format: Literal["walk-diary-input-v1"] = "walk-diary-input-v1"
    owner_id: Identifier
    walk_id: Identifier  # server ID, used for ownership and generation reservation
    client_session_id: Identifier  # App map/photo binding; not interchangeable with walk_id
    started_at: Instant
    ended_at: Instant
    pet_ids: tuple[Identifier, ...]
    evidence_origin: Literal["device", "mock", "mixed", "unknown"]
    route: RouteVersion
    records: tuple[UserRecord, ...] = Field(max_length=400)
    photos_status: Literal["complete", "not_available", "pending"]
    photo_manifest: PhotoManifestRef | None = None
    observations: tuple[MovementObservation, ...] = Field(default=(), max_length=200)
    backgrounds: tuple[SavedBackground, ...] = Field(default=(), max_length=2000)
    selected_background_ids: tuple[Identifier, ...] = ()
    scene_policy_version: Identifier
    writing_policy_version: Identifier

    @model_validator(mode="after")
    def source_scope(self):
        if self.photo_manifest is not None and self.photos_status != "complete":
            raise ValueError("a published photo manifest requires a complete server snapshot")
        if self.ended_at < self.started_at or len(set(self.pet_ids)) != len(self.pet_ids):
            raise ValueError("invalid session scope")
        records = {r.ref.identity: r for r in self.records}
        if len(records) != len(self.records) or len({o.id for o in self.observations}) != len(
            self.observations
        ):
            raise ValueError("snapshot requires unique source identities")
        for r in self.records:
            if (
                isinstance(r.content, Behavior)
                and r.content.pet_id is not None
                and r.content.pet_id not in self.pet_ids
            ):
                raise ValueError("action subject does not belong to this walk")
        for o in self.observations:
            if (
                self.route.status != "ready"
                or o.analysis_id != self.route.analysis_id
                or not self.started_at <= o.started_at <= o.ended_at <= self.ended_at
            ):
                raise ValueError("observation crosses finalized route scope")
        materials = {material_ref(m).identity: m for m in (*self.records, *self.observations)}
        by_id = {b.id: b for b in self.backgrounds}
        if len(by_id) != len(self.backgrounds):
            raise ValueError("duplicate background ID")
        selected = set(self.selected_background_ids)
        if len(selected) != len(self.selected_background_ids) or not selected <= by_id.keys():
            raise ValueError("unknown/duplicate selected background")
        for b in self.backgrounds:
            material = materials.get(b.target.identity)
            if (
                material is None
                or b.target != material_ref(material)
                or (isinstance(material, UserRecord) and material.deleted)
            ):
                raise ValueError("background targets stale, deleted or unknown source")
            if len({r.identity for r in b.supporting_targets}) != len(b.supporting_targets):
                raise ValueError("duplicate supporting target")
            for support in b.supporting_targets:
                related = materials.get(support.identity)
                if (
                    related is None
                    or material_ref(related) != support
                    or (isinstance(related, UserRecord) and related.deleted)
                ):
                    raise ValueError("background has stale supporting target")
            if (
                b.status in {"known", "partial", "empty"}
                and set(b.tags) & {"space", "environment"}
                and (
                    material.anchor.point is None
                    or material.anchor.position_state == "provisional"
                    or b.query_point != material.anchor.point
                )
            ):
                raise ValueError("spatial background requires a confirmed target")
            if (
                b.temporal_basis == "event_observation"
                and b.status in {"known", "partial"}
                and not b.valid_from <= material.anchor.event_at <= b.valid_until
            ):
                raise ValueError("event-time context does not cover its target")
        return self

    def revision(self) -> str:
        # Deeply revalidate mutable provider payloads before hashing or publishing.
        value = DiaryInput.model_validate(self.model_dump(mode="json")).model_dump(mode="json")
        value["records"].sort(key=lambda r: (r["ref"]["store"], r["ref"]["id"]))
        value["observations"].sort(key=lambda o: o["id"])
        value["backgrounds"].sort(key=lambda b: b["id"])
        value["selected_background_ids"].sort()
        value["pet_ids"].sort()
        return digest(value)
