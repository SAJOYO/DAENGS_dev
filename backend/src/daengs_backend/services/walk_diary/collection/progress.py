"""Invocation-local acquisition ledger. Freeze before cancellation or slot projection.

The scoped context bridges existing collector(board) wrappers without changing their
injection contract. It is installed by the graph and inherited only by its collector
task; direct/legacy collectors create their own ledger. Never a shared result cache.
"""

import asyncio
from contextvars import ContextVar

from daengs_walk.diary.board.backgrounds import (
    SceneBackgroundSnapshot,
    board_background_revision,
    scene_background_targets,
)

active_collection: ContextVar["CollectionProgress | None"] = ContextVar(
    "diary_collection_progress", default=None
)


class CollectionProgress:
    def __init__(self, board, deadline):
        self.board = board
        self.deadline = deadline
        self.template = SceneBackgroundSnapshot(
            board_revision=board_background_revision(board), targets=scene_background_targets(board)
        )
        self.expected = {}
        self.target_order = {
            target.core_ref.identity: i for i, target in enumerate(self.template.targets)
        }
        self.started = set()
        self.finished = {}
        self.frozen = None

    def open(self):
        return self.frozen is None and asyncio.get_running_loop().time() < self.deadline

    def expect(self, key, backgrounds):
        if self.frozen is None:
            self.expected[key] = self._checked(backgrounds)

    def start(self, key):
        if not self.open():
            return False
        self.started.add(key)
        return True

    def finish(self, key, backgrounds):
        if self.open():
            checked = self._checked(backgrounds)
            if self.open():
                self.finished[key] = checked

    def _checked(self, backgrounds):
        # Validation also copies mutable payload dictionaries across the ownership boundary.
        return (
            self.template.model_copy(update={"backgrounds": tuple(backgrounds)})
            .validate_board(self.board)
            .backgrounds
        )

    def accept(self, snapshot):
        """Atomic collectors remain supported; progressive collectors already froze their ledger."""
        checked = snapshot.validate_board(self.board)
        if self.frozen is not None and self.frozen != checked:
            raise ValueError("collector changed its frozen snapshot")
        if self.open():
            self.frozen = checked

    def freeze(self):
        if self.frozen is None:
            rows = []
            for key, pending in self.expected.items():
                rows.extend(
                    self.finished.get(key)
                    or tuple(
                        row.model_copy(
                            update={
                                "reason": "collection_timeout"
                                if key in self.started
                                else row.reason
                                if row.status == "not_requested" or row.reason == "scene_limit"
                                else "collection_not_started"
                            }
                        )
                        for row in pending
                    )
                )
            rows.sort(key=lambda row: self.target_order[row.target.identity])
            self.frozen = self.template.model_copy(
                update={"backgrounds": tuple(rows)}
            ).validate_board(self.board)
        return self.frozen.model_copy(deep=True)


def collection_progress(board, timeout_s):
    progress = active_collection.get()
    if progress is not None:
        if progress.template.board_revision != board_background_revision(board):
            raise ValueError("collector belongs to another board")
        progress.deadline = min(progress.deadline, asyncio.get_running_loop().time() + timeout_s)
        return progress
    return CollectionProgress(board, asyncio.get_running_loop().time() + timeout_s)
