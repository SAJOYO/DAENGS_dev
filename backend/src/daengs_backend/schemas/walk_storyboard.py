import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from daengs_walk.storyboard import (
    StoryboardBundle,
    StoryboardBundleV2,
    StoryboardBundleV3,
    StoryboardBundleV4,
)

BundleFormat = Literal[
    "walk-storyboard-candidates-v1",
    "walk-storyboard-candidates-v2",
    "walk-storyboard-candidates-v3",
    "walk-storyboard-candidates-v4",
]


class StoryboardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_entries: dict[uuid.UUID, int] = Field(max_length=200)
    refresh: bool = False
    bundle_format: BundleFormat = "walk-storyboard-candidates-v1"


class StoryboardResponse(BaseModel):
    session_id: uuid.UUID
    generation: int = Field(ge=0)
    input_revision: str
    status: Literal["pending", "running", "ready", "failed", "stale"]
    entry_revisions: dict[str, int]
    bundle: (
        StoryboardBundleV4 | StoryboardBundleV3 | StoryboardBundleV2 | StoryboardBundle | None
    ) = None
    error_code: str | None = None
