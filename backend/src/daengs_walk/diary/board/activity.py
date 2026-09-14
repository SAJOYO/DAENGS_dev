"""Small, phase-specific movement claims shared by wire and result validation."""

from datetime import datetime

from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.route.patterns import PATTERN_CASES

MEANINGS = {
    **{key: next(iter(value.values())) for key, value in PATTERN_CASES.items()},
    "relative_slow": "이번 산책의 기준 속도보다 상대적으로 느린 이동",
    "relative_fast": "이번 산책의 기준 속도보다 상대적으로 빠른 이동",
}


def movement_uses(request):
    uses = []
    for item in request.get("movement", []):
        facts = item["facts"]
        if facts.get("format") != "diary-movement-material-v1":
            raise ValueError("unsupported activity movement")
        claims = {c["id"]: c for c in facts["claims"]}
        for phase in facts["phases"]:
            for ref in phase["claims"]:
                claim = claims[ref]
                left, right = phase["start_s"], phase["end_s"]
                if not claim["start_s"] <= left < right <= claim["end_s"]:
                    raise ValueError("phase exceeds original claim")
                if claim["meaning"] not in MEANINGS:
                    raise ValueError("unknown movement meaning")
                uses.append(
                    {
                        "id": "movement-use:" + digest([item["id"], ref, left, right]),
                        "slot_id": item["id"],
                        "source_id": ref,
                        "kind": claim["kind"],
                        "meaning": claim["meaning"],
                        "from_s": left - facts["scene_at_s"],
                        "to_s": right - facts["scene_at_s"],
                        "support_from_s": claim["start_s"] - facts["scene_at_s"],
                        "support_to_s": claim["end_s"] - facts["scene_at_s"],
                        **(
                            {"at_s": claim["event_s"] - facts["scene_at_s"]}
                            if "event_s" in claim
                            else {}
                        ),
                    }
                )
    if len({u["id"] for u in uses}) != len(uses):
        raise ValueError("duplicate activity claim")
    if {u["slot_id"] for u in uses} != {m["id"] for m in request.get("movement", [])}:
        raise ValueError("selected movement has no writing claims")
    return uses


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
