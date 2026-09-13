"""Collected backgrounds bound to an already selected board, including route cores.

These are per-request snapshots, not records, a shared cache, or a publication.
Existing record snapshots and their revisions remain unchanged.
"""

from pydantic import Field

from daengs_walk.diary_board import BaseBoard
from daengs_walk.diary_input import (
    Anchor,
    DiaryContract,
    Digest,
    Identifier,
    MaterialRef,
    SavedBackground,
    digest,
)


def board_background_revision(board: BaseBoard):
    return digest(
        {
            "session": board.client_session_id,
            "input": board.input_revision,
            "plan": board.plan_revision,
            "scenes": [
                {
                    "id": s.id,
                    "core": s.core_ref.model_dump(mode="json"),
                    "anchor": s.anchor.model_dump(mode="json"),
                }
                for s in board.scenes
            ],
        }
    )


class SceneBackgroundTarget(DiaryContract):
    scene_id: Identifier
    core_ref: MaterialRef
    anchor: Anchor


class SceneBackgroundSnapshot(DiaryContract):
    board_revision: Digest
    targets: tuple[SceneBackgroundTarget, ...] = Field(max_length=602)
    backgrounds: tuple[SavedBackground, ...] = Field(default=(), max_length=2408)

    def validate_board(self, board):
        value = SceneBackgroundSnapshot.model_validate(self.model_dump(mode="json"))
        if value.board_revision != board_background_revision(board):
            raise ValueError("scene backgrounds belong to another selected board")
        expected = {
            s.id: SceneBackgroundTarget(scene_id=s.id, core_ref=s.core_ref, anchor=s.anchor)
            for s in board.scenes
        }
        if len({t.scene_id for t in value.targets}) != len(value.targets) or any(
            expected.get(t.scene_id) != t for t in value.targets
        ):
            raise ValueError("scene background target changed")
        targets = {t.core_ref.identity: t for t in value.targets}
        if len(targets) != len(value.targets) or len({b.id for b in value.backgrounds}) != len(
            value.backgrounds
        ):
            raise ValueError("duplicate scene background identity")
        for saved in value.backgrounds:
            target = targets.get(saved.target.identity)
            if target is None or saved.target != target.core_ref or saved.supporting_targets:
                raise ValueError("scene background requires its exact core version")
            if saved.query_point != target.anchor.point:
                raise ValueError("scene background query position changed")
            if target.anchor.point is None and saved.status != "not_requested":
                raise ValueError("unlocated scene cannot have a spatial query")
        return value


def scene_background_targets(board):
    return tuple(
        SceneBackgroundTarget(scene_id=s.id, core_ref=s.core_ref, anchor=s.anchor)
        for s in board.scenes
    )
