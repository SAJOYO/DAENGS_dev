"""Small, phase-specific movement claims shared by wire and result validation."""

from datetime import datetime

from daengs_walk.diary.route.pin_context import MEANINGS, movement_uses


def activity_projection(request):
    from daengs_walk.diary.board.action_context import project_action

    return project_action(request)


def require_activity_transfer(request, payload, references):
    """Invocation guard: the actual request must match the pin-scoped projection."""
    expected, refs = activity_projection(request)
    if payload != expected or references != refs:
        raise ValueError("selected movement lost at model boundary")


def activity_fallback(request):
    from daengs_walk.diary.board.action_context import require_action

    action = require_action(request)
    name = action["actor"].get("name")
    text = (f"{name}의 " if name else "") + action["material"]["무엇을"] + " 행동을 기록했다."
    return text, ()


def covers_observation(request, used_ids, observation):
    if observation is None or observation.kind not in {"observed_slow", "observed_fast"}:
        return False
    at = datetime.fromisoformat(request["event_at"])
    start = (observation.started_at - at).total_seconds()
    end = (observation.ended_at - at).total_seconds()
    meaning = "relative_slow" if observation.kind == "observed_slow" else "relative_fast"
    spans = sorted(
        (u["from_s"], u["to_s"])
        for u in movement_uses(request)
        if u["id"] in used_ids and u["meaning"] == meaning
    )
    cursor = start
    for left, right in spans:
        if right <= cursor:
            continue
        if left > cursor:
            break
        cursor = right
    return cursor >= end


__all__ = ["MEANINGS", "movement_uses"]
