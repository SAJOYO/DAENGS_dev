"""Public relational read model; private prompts, reviews and provider payloads stay in storage."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from daengs_walk.diary.contracts.input import Anchor, PhotoManifestRef, UserRecord
from daengs_walk.diary.relational.scene_comparison_contracts import SceneCardHeader, SceneSnapshot

RELATIONAL_FORMAT = "walk-relational-diary-v1"
RELATIONAL_STORAGE = "walk-relational-diary-storage-v1"
RELATIONAL_PENDING = "walk-relational-diary-pending-v1"


class PublicValue(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiaryPart(PublicValue):
    status: Literal["returned", "failed", "not_requested"]
    text: str
    semantic_status: Literal["model_reviewed", "unverified", "not_published"]


class RelationalCard(PublicValue):
    scene_id: str
    anchor: Anchor
    header: SceneCardHeader
    space: DiaryPart
    action: DiaryPart
    body: str  # Empty is valid: the card can consist of its original/photo/context.
    current_context: SceneSnapshot
    comparison_scene_id: str | None
    originals: list[UserRecord]


class RelationalBundle(PublicValue):
    format: Literal["walk-relational-diary-v1"] = RELATIONAL_FORMAT
    client_session_id: uuid.UUID
    title: str | None
    title_status: Literal["returned", "failed", "not_requested"]
    cards: list[RelationalCard]


class RelationalDiaryResponse(PublicValue):
    format: Literal["walk-relational-diary-response-v1"] = "walk-relational-diary-response-v1"
    session_id: uuid.UUID
    generation: int = Field(ge=0)
    input_revision: str
    status: Literal["pending", "running", "ready", "failed", "stale"]
    entry_revisions: dict[str, int]
    photos_status: Literal["complete", "not_available"]
    photo_manifest: PhotoManifestRef | None
    target_scene_count: int
    bundle: RelationalBundle | None = None
    error_code: str | None = None
    # Effective server limits, not model internals or a claim of complete prose.
    execution_limits: dict[str, JsonValue] = Field(default_factory=dict)
