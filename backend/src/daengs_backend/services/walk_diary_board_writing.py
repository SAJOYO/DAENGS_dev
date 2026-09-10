"""Carry the existing writer's background prose into the fixed base-board plan."""

from daengs_walk.diary_board import VerifiedBoardRoute
from daengs_walk.diary_board_assembly import assemble_base_board
from daengs_walk.diary_board_output import publish_board
from daengs_walk.diary_output import DiaryBundle, WritingReceipt


def complete_board(prepared, output):
    output = DiaryBundle.model_validate(output)
    source, base = prepared.input.source, prepared.board
    if (
        output.input_revision != source.revision()
        or output.plan_revision != prepared.prepared.plan.revision()
    ):
        raise ValueError("writer returned another plan's bundle")
    observation = prepared.input.observation_source
    route = (
        VerifiedBoardRoute(observation.route, observation.evidence)
        if observation and observation.evidence
        else None
    )
    receipt = None
    if output.model_status == "accepted":
        receipt = WritingReceipt(
            plan_revision=base.plan.revision(),
            writing={
                "title": output.title,
                "scenes": [
                    {
                        "scene_id": s.id,
                        "text": s.narration.text,
                        "evidence_ids": s.narration.evidence_ids,
                    }
                    for s in output.scenes
                    if s.narration.status in {"generated", "omitted"}
                ],
            },
        )
    board = assemble_base_board(
        source, base.plan, route=route, receipt=receipt, failure_code=output.failure_code
    )
    return publish_board(board, base.plan)
