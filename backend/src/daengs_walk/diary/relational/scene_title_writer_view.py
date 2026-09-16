"""Model-facing title policy; internal revisions stay outside this projection."""

from daengs_walk.diary.relational.writer_meaning import present
from daengs_walk.diary.relational.writer_time import DIARY_TIMEZONE, local_writer_times

SCENE_TITLE_WRITER_POLICY = "adopted-scene-prose-title-v1"


def scene_title_writer_view(context):
    if context is None:
        return None
    return local_writer_times(
        {
            "version": SCENE_TITLE_WRITER_POLICY,
            "scene_id": context.scene_id,
            "timezone": DIARY_TIMEZONE,
            "recorded_at": context.recorded_at.isoformat(),
            **present(space=context.space, action=context.action),
        }
    )
