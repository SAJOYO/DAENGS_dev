"""Current diary/board response and existing saved bundle validation."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from daengs_walk.diary.board.output import BOARD_FORMAT, BOARD_RESPONSE, PublishedBoard
from daengs_walk.diary.contracts.input import PhotoManifestRef
from daengs_walk.diary.contracts.output import DiaryBundle


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
