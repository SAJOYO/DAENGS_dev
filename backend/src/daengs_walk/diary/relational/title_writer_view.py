"""Title writing reads adopted diary prose, not internal movement report strings."""

from daengs_walk.diary.relational.writer_meaning import present

TITLE_WRITER_POLICY = "adopted-prose-title-v2"


def title_writer_view(context):
    return {
        "version": TITLE_WRITER_POLICY,
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


def title_publication_view(context, policy):
    if policy is None:  # Frozen historical v1 readmodel was also the writer input.
        return context.model_dump(mode="json")
    if policy == TITLE_WRITER_POLICY:
        return title_writer_view(context)
    raise ValueError("unsupported title writer policy")
