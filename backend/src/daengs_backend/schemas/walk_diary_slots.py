"""Owner-only preview options. Source records/GPS are read from storage, never this DTO."""

from pydantic import Field

from daengs_walk.diary.board.preview import SlotPreview
from daengs_walk.diary.contracts.input import DiaryContract
from daengs_walk.diary.contracts.slots import SlotPolicy


class SlotPreviewRequest(DiaryContract):
    target_scene_count: int = Field(ge=1, le=50)
    policy: SlotPolicy = Field(default_factory=SlotPolicy)
    generate: bool = True
    collect_backgrounds: bool = False


class SlotPreviewResponse(DiaryContract):
    preview: SlotPreview
    context_pending: bool
    excluded_backgrounds: tuple[dict[str, str], ...]
