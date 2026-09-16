"""HTTP generation request and negotiated format options."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from daengs_backend.schemas.walk_relational_diary import RELATIONAL_FORMAT
from daengs_walk.diary.board.output import BOARD_FORMAT
from daengs_walk.diary.contracts.input import PhotoManifestRef

BundleFormat = Literal[
    "walk-storyboard-candidates-v1",
    "walk-storyboard-candidates-v2",
    "walk-storyboard-candidates-v3",
    "walk-storyboard-candidates-v4",
    "walk-storyboard-candidates-v5",
    "walk-diary-bundle-v1",
    "walk-diary-board-v1",
    "walk-relational-diary-v1",
]

MAX_PREPARATION_BUDGET_MS = 20_000


class StoryboardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_entries: dict[uuid.UUID, int] = Field(max_length=200)
    refresh: bool = False
    bundle_format: BundleFormat = "walk-storyboard-candidates-v1"
    target_scene_count: int | None = Field(default=None, ge=1, le=50)
    expected_photo_manifest: PhotoManifestRef | None = None
    preparation_budget_ms: int | None = Field(default=None, ge=0, le=MAX_PREPARATION_BUDGET_MS)

    @model_validator(mode="after")
    def diary_options(self):
        if self.preparation_budget_ms is not None and self.bundle_format != BOARD_FORMAT:
            raise ValueError("publication budget requires the base board format")
        if self.bundle_format in {"walk-diary-bundle-v1", BOARD_FORMAT, RELATIONAL_FORMAT}:
            if self.target_scene_count is None:
                raise ValueError("diary requires an explicit target_scene_count")
        elif self.target_scene_count is not None or self.expected_photo_manifest is not None:
            raise ValueError("diary options require the diary format")
        return self
