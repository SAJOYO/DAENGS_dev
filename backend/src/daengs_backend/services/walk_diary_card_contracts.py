"""Card writing wire contracts; no provider, policy, graph or runtime imports."""

from typing import Literal

from pydantic import Field, JsonValue

from daengs_walk.diary_board_output import PublishedBoard
from daengs_walk.diary_input import DiaryContract, Digest, Identifier
from daengs_walk.diary_scene_backgrounds import SceneBackgroundSnapshot

MAX_CARDS = 12


class SpaceProse(DiaryContract):
    card_id: Identifier
    request_revision: Digest
    text: str = Field(max_length=220)
    evidence_ids: tuple[Identifier, ...] = Field(max_length=17)


class ActionProse(DiaryContract):
    card_id: Identifier
    request_revision: Digest
    text: str = Field(min_length=1, max_length=140)
    action_id: Identifier


class CardTitle(DiaryContract):
    card_id: Identifier
    content_revision: Digest
    text: str = Field(min_length=1, max_length=80)


class CardTitles(DiaryContract):
    titles: tuple[CardTitle, ...] = Field(max_length=MAX_CARDS)


class WritingJob(DiaryContract):
    stage: Literal["space", "action", "title"]
    request_revision: Digest
    request: dict[str, JsonValue]
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    # Only accepted output is retained; failures never persist raw provider errors.
    accepted: dict[str, JsonValue] | None = None
    reused: bool = Field(default=False, exclude_if=lambda value: not value)
    failure_code: Literal["provider_failed", "invalid_response", "budget_exceeded"] | None = None


class CardWritingResult(DiaryContract):
    format: Literal["diary-card-writing-v1"] = "diary-card-writing-v1"
    input_revision: Digest
    plan_revision: Digest
    slot_revision: Digest
    writer_version: Digest
    bundle: PublishedBoard
    jobs: tuple[WritingJob, ...]
    scene_backgrounds: SceneBackgroundSnapshot | None = None
