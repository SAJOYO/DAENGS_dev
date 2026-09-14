"""Negotiate stored formats before choosing current diary or historical generation."""

from daengs_backend.services.walk_diary.api import existing_format
from daengs_walk.diary.board.output import BOARD_FORMAT


async def _default_titles(bundle):
    from daengs_backend.services.walk_legacy.titles import title_storyboard

    return await title_storyboard(bundle)


async def get(
    session,
    owner,
    walk_id,
    bundle_format="walk-storyboard-candidates-v1",
    *,
    target_scene_count=None,
):
    if bundle_format == BOARD_FORMAT:
        bundle_format, target_scene_count = await existing_format(
            session, owner, walk_id, target_scene_count
        )
    if bundle_format in {"walk-diary-bundle-v1", BOARD_FORMAT}:
        from daengs_backend.services.walk_diary.api import get_diary

        return await get_diary(session, owner, walk_id, target_scene_count, bundle_format)
    from daengs_backend.services.walk_legacy import storyboard

    return await storyboard.get(session, owner, walk_id, bundle_format)


async def generate(
    session, owner, walk_id, request, lookup, titles=_default_titles, *, diary_writer=None
):
    if request.bundle_format == BOARD_FORMAT:
        chosen, target = await existing_format(session, owner, walk_id, request.target_scene_count)
        request = request.model_copy(
            update={
                "bundle_format": chosen,
                "target_scene_count": target,
                "expected_photo_manifest": request.expected_photo_manifest
                if target is not None
                else None,
                "refresh": False,
                "preparation_budget_ms": request.preparation_budget_ms
                if chosen == BOARD_FORMAT
                else None,
            }
        )
    if request.bundle_format in {"walk-diary-bundle-v1", BOARD_FORMAT}:
        from daengs_backend.services.walk_diary.api import generate_diary

        return await generate_diary(session, owner, walk_id, request, writer=diary_writer)
    from daengs_backend.services.walk_legacy import storyboard

    return await storyboard.generate(session, owner, walk_id, request, lookup, titles)
