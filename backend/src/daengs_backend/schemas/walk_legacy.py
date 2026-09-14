"""Supported historical candidate response, independent of diary response contracts."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from daengs_walk.storyboard import (
    StoryboardBundle,
    StoryboardBundleV2,
    StoryboardBundleV3,
    StoryboardBundleV4,
    StoryboardBundleV5,
)


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
