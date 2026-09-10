import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from daengs_walk.diary_board_output import BOARD_FORMAT, BOARD_RESPONSE, PublishedBoard
from daengs_walk.diary_input import PhotoManifestRef
from daengs_walk.diary_output import DiaryBundle
from daengs_walk.storyboard import (
    StoryboardBundle,
    StoryboardBundleV2,
    StoryboardBundleV3,
    StoryboardBundleV4,
    StoryboardBundleV5,
)

BundleFormat = Literal[
    "walk-storyboard-candidates-v1",
    "walk-storyboard-candidates-v2",
    "walk-storyboard-candidates-v3",
    "walk-storyboard-candidates-v4",
    "walk-storyboard-candidates-v5",
    "walk-diary-bundle-v1",
    "walk-diary-board-v1",
]


class StoryboardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_entries: dict[uuid.UUID, int] = Field(max_length=200)
    refresh: bool = False
    bundle_format: BundleFormat = "walk-storyboard-candidates-v1"
    target_scene_count: int | None = Field(default=None, ge=1, le=50)
    expected_photo_manifest: PhotoManifestRef | None = None
    preparation_budget_ms: int | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def diary_options(self):
        if self.preparation_budget_ms is not None and self.bundle_format != BOARD_FORMAT:
            raise ValueError("publication budget requires the base board format")
        if self.bundle_format in {"walk-diary-bundle-v1", BOARD_FORMAT}:
            if self.target_scene_count is None:
                raise ValueError("diary requires an explicit target_scene_count")
        elif self.target_scene_count is not None or self.expected_photo_manifest is not None:
            raise ValueError("diary options require the diary format")
        return self


class StoryboardResponse(BaseModel):
    session_id: uuid.UUID
    generation: int = Field(ge=0)
    input_revision: str
    status: Literal["pending", "running", "ready", "failed", "stale"]
    entry_revisions: dict[str, int]
    bundle: (
        StoryboardBundleV5
        | StoryboardBundleV4
        | StoryboardBundleV3
        | StoryboardBundleV2
        | StoryboardBundle
        | None
    ) = None
    error_code: str | None = None


class DiaryStoryboardResponse(BaseModel):
    format: Literal["walk-diary-response-v1", "walk-diary-board-response-v1"]
    session_id: uuid.UUID
    generation: int = Field(ge=0)
    input_revision: str
    status: Literal["pending", "running", "ready", "failed", "stale"]
    entry_revisions: dict[str, int]
    photos_status: Literal["complete", "not_available"]
    photo_manifest: PhotoManifestRef | None
    target_scene_count: int
    preparation_counts: dict[str, int]
    preparation_limits: tuple[str, ...]
    # The returned ready bundle keeps its saved revision; never auto-regenerate for this flag.
    background_update_available: bool = False
    bundle: DiaryBundle | PublishedBoard | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def matching_bundle_format(self):
        if self.bundle is not None and (
            (self.format == BOARD_RESPONSE) != (self.bundle.format == BOARD_FORMAT)
            or str(self.session_id) != self.bundle.client_session_id
        ):
            raise ValueError("diary response does not match its board format/session")
        return self
