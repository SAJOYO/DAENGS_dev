"""Compatibility schema imports for the unchanged storyboard HTTP endpoint."""

from daengs_backend.schemas.walk_diary import DiaryStoryboardResponse
from daengs_backend.schemas.walk_generation import (
    MAX_PREPARATION_BUDGET_MS,
    BundleFormat,
    StoryboardRequest,
)
from daengs_backend.schemas.walk_legacy import StoryboardResponse

__all__ = [
    "MAX_PREPARATION_BUDGET_MS",
    "BundleFormat",
    "DiaryStoryboardResponse",
    "StoryboardRequest",
    "StoryboardResponse",
]
