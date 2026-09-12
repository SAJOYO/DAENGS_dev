"""Board JSONB receipt on the existing storyboard row. No schema or source-copy migration."""

from typing import Literal

from pydantic import Field, model_validator

from daengs_backend.services.walk_diary_board_provenance import writing_receipt
from daengs_walk.diary_board_output import PublishedBoard
from daengs_walk.diary_board_receipt import StoredSlotWriting
from daengs_walk.diary_input import DiaryContract, Digest, digest
from daengs_walk.diary_scene_backgrounds import SceneBackgroundSnapshot

STORAGE_FORMAT = "walk-diary-board-storage-v2"


class LegacyStoredBoard(DiaryContract):
    format: Literal["walk-diary-board-storage-v1"] = "walk-diary-board-storage-v1"
    generation_revision: Digest
    source_revision: Digest
    bundle_sha256: Digest
    bundle: PublishedBoard
    preparation_counts: dict[str, int]
    preparation_limits: tuple[str, ...]

    @model_validator(mode="after")
    def intact(self):
        if digest(self.bundle) != self.bundle_sha256:
            raise ValueError("stored board changed without its receipt")
        return self


class StoredBoard(LegacyStoredBoard):
    format: Literal["walk-diary-board-storage-v2"] = STORAGE_FORMAT
    writing_receipt: StoredSlotWriting
    writing_receipt_sha256: Digest
    scene_backgrounds: SceneBackgroundSnapshot | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    scene_backgrounds_sha256: Digest | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def intact_writing(self):
        if self.scene_backgrounds_sha256 != (
            digest(self.scene_backgrounds) if self.scene_backgrounds is not None else None
        ):
            raise ValueError("stored scene backgrounds changed without their receipt")
        if digest(self.writing_receipt) != self.writing_receipt_sha256:
            raise ValueError("stored writing changed without its receipt")
        self.writing_receipt.require_bundle(self.bundle, self.generation_revision)
        return self


def load_board(raw):
    if isinstance(raw, dict) and raw.get("format") == "walk-diary-board-storage-v1":
        return LegacyStoredBoard.model_validate(raw)
    return StoredBoard.model_validate(raw)


def source_revision(prepared):
    source = prepared.input.source.model_copy(
        update={"backgrounds": (), "selected_background_ids": ()}
    )
    # Policy changes cannot replace an already published board. Source edits still invalidate it.
    source = source.model_copy(
        update={"scene_policy_version": "published", "writing_policy_version": "published"}
    )
    return source.revision()


def store_board(prepared, bundle, revision, *, writing=None):
    bundle = PublishedBoard.model_validate(bundle)
    source, plan = prepared.input.source, prepared.board.plan
    if (
        bundle.client_session_id != source.client_session_id
        or bundle.input_revision != source.revision()
        or bundle.plan_revision != plan.revision()
    ):
        raise ValueError("stored board binding mismatch")
    receipt = writing_receipt(prepared, bundle, revision, writing)
    return StoredBoard(
        generation_revision=revision,
        source_revision=source_revision(prepared),
        bundle_sha256=digest(bundle),
        bundle=bundle,
        writing_receipt=receipt,
        writing_receipt_sha256=digest(receipt),
        scene_backgrounds=prepared.board.scene_backgrounds,
        scene_backgrounds_sha256=(
            digest(prepared.board.scene_backgrounds)
            if prepared.board.scene_backgrounds is not None
            else None
        ),
        preparation_counts=plan.counts,
        preparation_limits=plan.limits,
    ).model_dump(mode="json")


def read_board(prepared, row, revision):
    stored = load_board(row.bundle)
    if (
        stored.generation_revision != row.input_revision
        or stored.bundle.client_session_id != prepared.input.source.client_session_id
    ):
        raise ValueError("stored board belongs to another generation/session")
    return stored if stored.source_revision == source_revision(prepared) else None
