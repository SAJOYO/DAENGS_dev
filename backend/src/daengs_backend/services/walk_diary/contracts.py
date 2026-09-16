"""Card writing wire contracts; no provider, policy, graph or runtime imports."""

from typing import Literal

from pydantic import Field, JsonValue

from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
from daengs_walk.diary.board.output import PublishedBoard
from daengs_walk.diary.contracts.input import DiaryContract, Digest, Identifier

MAX_CARDS = 12


class SpaceProse(DiaryContract):
    card_id: Identifier
    request_revision: Digest
    text: str = Field(max_length=220)
    evidence_ids: tuple[Identifier, ...] = Field(max_length=17)


class ActionProse(DiaryContract):
    card_id: Identifier
    request_revision: Digest
    text: str = Field(min_length=1, max_length=220)
    action_id: Identifier | None = None
    movement_ids: tuple[Identifier, ...] = Field(default=(), exclude_if=lambda v: not v)


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
    # Normalized material. When tool_trace exists, its initial_input + tool results
    # are the actual supplied facts; this complete seed stays server-side.
    llm_request: dict[str, JsonValue] | None = Field(default=None, exclude_if=lambda v: v is None)
    tool_trace: dict[str, JsonValue] | None = Field(default=None, exclude_if=lambda v: v is None)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    # Only accepted output is retained; failures never persist raw provider errors.
    accepted: dict[str, JsonValue] | None = None
    reused: bool = Field(default=False, exclude_if=lambda value: not value)
    failure_code: (
        Literal["provider_failed", "invalid_response", "invalid_input", "budget_exceeded"] | None
    ) = None


class CollectionReceipt(DiaryContract):
    """Acquired inputs survive projection failures; no provider exceptions or credentials."""

    snapshot: SceneBackgroundSnapshot
    status: Literal["completed", "timeout", "error"]
    failure_code: Literal["collector_failed", "invalid_snapshot"] | None = None
    application_status: Literal["applied", "partial", "failed"] = "applied"
    application_failures: tuple[Identifier, ...] = Field(default=(), max_length=2408)


class CardWritingResult(DiaryContract):
    format: Literal["diary-card-writing-v1"] = "diary-card-writing-v1"
    input_revision: Digest
    plan_revision: Digest
    slot_revision: Digest
    writer_version: Digest
    bundle: PublishedBoard
    jobs: tuple[WritingJob, ...]
    scene_backgrounds: SceneBackgroundSnapshot | None = None
    collection_receipt: CollectionReceipt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
