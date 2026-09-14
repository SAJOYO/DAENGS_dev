"""Historical cited facts, independent of today's selection and writing policies."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from daengs_walk.diary.contracts.input import DiaryContract, Digest, Identifier, digest
from daengs_walk.diary.contracts.slots import Part, SlotSource


class CitedEvidence(DiaryContract):
    id: Identifier
    part: Part
    role: Identifier
    facts: dict[str, JsonValue]
    sources: tuple[SlotSource, ...] = Field(min_length=1)


class StoredSceneWriting(DiaryContract):
    scene_id: Identifier
    original_body_sha256: Digest
    background: str = Field(max_length=220)
    evidence: tuple[CitedEvidence, ...] = Field(max_length=17)
    # Preserve old JSON bytes/hashes. The historical field holds the generated text.
    composition: Literal["prepend", "replace"] = Field(
        default="prepend", exclude_if=lambda v: v == "prepend"
    )
    action_id: Identifier | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def cited(self):
        if len({e.id for e in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate stored citation")
        if self.background != self.background.strip() or bool(self.background) != bool(
            self.evidence or self.action_id
        ):
            raise ValueError("stored prose requires its citations")
        if self.composition == "replace" and not self.background:
            raise ValueError("replacement requires generated text")
        return self


class StoredSlotWriting(DiaryContract):
    generation_revision: Digest
    input_revision: Digest
    plan_revision: Digest
    bundle_sha256: Digest
    slot_revision: Digest
    # Store the historical values; do not reinterpret them with today's policy model.
    slot_policy: dict[str, JsonValue]
    writer: dict[str, JsonValue]
    writer_version: Digest
    scenes: tuple[StoredSceneWriting, ...] = Field(min_length=2, max_length=602)

    @model_validator(mode="after")
    def versioned(self):
        if self.writer_version != digest(self.writer):
            raise ValueError("stored writer differs from its version")
        if len({s.scene_id for s in self.scenes}) != len(self.scenes):
            raise ValueError("duplicate stored writing scene")
        return self

    def require_bundle(self, bundle, generation_revision):
        if (
            self.generation_revision != generation_revision
            or self.input_revision != bundle.input_revision
            or self.plan_revision != bundle.plan_revision
            or self.bundle_sha256 != digest(bundle)
            or [s.scene_id for s in self.scenes] != [s.id for s in bundle.scenes]
        ):
            raise ValueError("stored writing belongs to another board/generation")
        for saved, scene in zip(self.scenes, bundle.scenes, strict=True):
            if saved.composition == "replace":
                matches = scene.body == saved.background
            else:
                prefix = saved.background + "\n" if saved.background else ""
                matches = scene.body.startswith(prefix) and (
                    digest(scene.body[len(prefix) :]) == saved.original_body_sha256
                )
            if not matches:
                raise ValueError("stored prose and original body no longer compose this scene")
            if saved.action_id and saved.action_id != "action:" + digest(scene.core):
                raise ValueError("stored action belongs to another scene core")
            if bundle.model_status != "accepted" and (saved.evidence or saved.action_id):
                raise ValueError("unwritten board cannot claim AI citations")
