"""Title writing reads adopted diary prose, not internal movement report strings."""

from daengs_walk.diary.relational.writer_meaning import present
from daengs_walk.diary.relational.writer_time import DIARY_TIMEZONE, local_writer_times

TITLE_WRITER_POLICY = "adopted-prose-title-v3"
TITLE_WRITER_POLICIES = frozenset({"adopted-prose-title-v2", TITLE_WRITER_POLICY})


def _title_writer_view(context, policy):
    return {
        "version": policy,
        "scenes": [
            {
                "scene_id": s.scene_id,
                "order": s.order,
                "recorded_at": s.recorded_at.isoformat(),
                **present(space=s.space, action=s.action),
            }
            for s in context.scenes
            if s.space or s.action
        ],
    }


def title_writer_view(context):
    view = local_writer_times(_title_writer_view(context, TITLE_WRITER_POLICY))
    view["timezone"] = DIARY_TIMEZONE
    return view


def title_publication_view(context, policy):
    if policy is None:  # Frozen historical v1 readmodel was also the writer input.
        return context.model_dump(mode="json")
    if policy == TITLE_WRITER_POLICY:
        return title_writer_view(context)
    if policy == "adopted-prose-title-v2":
        return _title_writer_view(context, policy)
    raise ValueError("unsupported title writer policy")
