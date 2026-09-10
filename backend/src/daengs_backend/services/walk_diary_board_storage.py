"""Board JSONB receipt on the existing storyboard row. No schema or source-copy migration."""

from typing import Literal

from pydantic import model_validator

from daengs_walk.diary_board_output import PublishedBoard
from daengs_walk.diary_input import DiaryContract, Digest, digest

STORAGE_FORMAT = "walk-diary-board-storage-v1"


class StoredBoard(DiaryContract):
    format: Literal["walk-diary-board-storage-v1"] = STORAGE_FORMAT
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


def source_revision(prepared):
    source = prepared.input.source.model_copy(
        update={"backgrounds": (), "selected_background_ids": ()}
    )
    # Policy changes cannot replace an already published board. Source edits still invalidate it.
    source = source.model_copy(
        update={"scene_policy_version": "published", "writing_policy_version": "published"}
    )
    return source.revision()


def store_board(prepared, bundle, revision):
    bundle = PublishedBoard.model_validate(bundle)
    source, plan = prepared.input.source, prepared.board.plan
    if (
        bundle.client_session_id != source.client_session_id
        or bundle.input_revision != source.revision()
        or bundle.plan_revision != plan.revision()
    ):
        raise ValueError("stored board binding mismatch")
    return StoredBoard(
        generation_revision=revision,
        source_revision=source_revision(prepared),
        bundle_sha256=digest(bundle),
        bundle=bundle,
        preparation_counts=plan.counts,
        preparation_limits=plan.limits,
    ).model_dump(mode="json")


def read_board(prepared, row, revision):
    stored = StoredBoard.model_validate(row.bundle)
    if (
        stored.generation_revision != row.input_revision
        or stored.bundle.client_session_id != prepared.input.source.client_session_id
    ):
        raise ValueError("stored board belongs to another generation/session")
    return stored if stored.source_revision == source_revision(prepared) else None
