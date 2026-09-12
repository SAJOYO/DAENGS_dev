"""Read snapshot, release walk lock, then optional prose. No generation/publication writes."""

from daengs_backend.schemas.walk_diary_slots import SlotPreviewResponse
from daengs_backend.services.walk_diary_input import read_input
from daengs_backend.services.walk_diary_slot_writing import write_slot_preview
from daengs_backend.services.walk_diary_space_collection import configured_collection
from daengs_walk.diary_board import BaseBoardPolicy, VerifiedBoardRoute
from daengs_walk.diary_slots import prepare_board_slots, prepare_slot_preview
from daengs_walk.diary_stamps import StampPolicy


async def preview_saved_slots(
    session, owner, walk_id, request, *, writer=write_slot_preview, collector=configured_collection
):
    try:
        assembled = await read_input(session, owner, walk_id)
        observation = assembled.observation_source
        route = (
            VerifiedBoardRoute(observation.route, observation.evidence)
            if (observation is not None and observation.evidence is not None)
            else None
        )
        preview = prepare_slot_preview(
            assembled.source,
            request.policy,
            BaseBoardPolicy(
                intermediate=StampPolicy(target_scene_count=request.target_scene_count)
            ),
            route=route,
        )
        await session.commit()  # Release the read lock before the external model call.
    except BaseException:
        await session.rollback()
        raise
    if request.collect_backgrounds:
        collected = await collector(preview.base_board)
        slots = prepare_board_slots(
            assembled.source,
            preview.base_board,
            request.policy,
            route=route,
            scene_backgrounds=collected,
        )
        preview = preview.model_copy(update={"stamps": slots.stamps, "revision": slots.revision()})
    if request.generate:
        preview = await writer(preview)
    return SlotPreviewResponse(
        preview=preview,
        context_pending=assembled.context_pending,
        excluded_backgrounds=assembled.excluded_backgrounds,
    )
