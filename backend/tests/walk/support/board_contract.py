"""Deterministic cross-client contract from the real saved-route/base-board pipeline."""

from dataclasses import replace
from unittest.mock import patch
from uuid import UUID

from daengs_backend.services.walk_diary_base_board import assemble_saved_base_board
from daengs_backend.services.walk_diary_board_writing import complete_board
from daengs_backend.services.walk_diary_generation import result
from daengs_backend.services.walk_diary_prepare import PreparedWalkDiary
from daengs_walk.diary_input import Behavior, DiaryInput, digest
from daengs_walk.diary_writing import accept_writing, prepare_writing
from tests.walk.support.base_board import policy, saved_case
from tests.walk.support.diary import nearby, prose, record


def board_contract():
    samples = [(i * 10, min(i, 60) * 20 + max(i - 65, 0) * 20) for i in range(101)]
    with patch.multiple(
        "tests.walk.support.observations", WALK=UUID(int=2), OWNER=UUID(int=3), SESSION=UUID(int=1)
    ):
        assembled, _, _ = saved_case(samples)
    note = record("note", "05")
    action = record("action", "07").model_copy(
        update={"content": Behavior(kind="behavior", code="sniffing")}
    )
    photo = record("photo", "09", photo=True)
    bg = nearby(note)
    source = DiaryInput.model_validate(
        {
            **assembled.source.model_dump(mode="json"),
            "records": [note, action, photo],
            "photos_status": "complete",
            "photo_manifest": {"publisher_id": "publisher", "revision": 1},
            "backgrounds": [bg],
            "selected_background_ids": [bg.id],
        }
    )
    base = assemble_saved_base_board(replace(assembled, source=source), policy())
    prepared = PreparedWalkDiary(base.input, base.plan.intermediate, base)
    # Exercise the same accepted writer bridge as the API, without a live model call.
    old_plan = prepared.prepared.plan
    writing = prepare_writing(source, prepared.prepared)
    output = complete_board(
        prepared, accept_writing(source, old_plan, writing, prose(writing.payload))
    )
    response = result(prepared, None, digest("contract-generation")).model_copy(
        update={"status": "ready", "generation": 1, "bundle": output}
    )
    return response.model_dump(mode="json")
