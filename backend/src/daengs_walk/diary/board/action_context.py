"""A behavior pin opens writing; movement only describes the instant of that pin.

This is a consumption boundary, not a movement detector. Full analysis stays in
the stamp. Never extend a recorded instant into the duration or cause of behavior.
"""

from datetime import datetime

from daengs_walk.diary.board.scene_input import ACTION_MEANINGS
from daengs_walk.diary.contracts.input import digest

SHAPE_TEXT = {
    "straight_run": "대체로 곧게 이동",
    "curve_left": "왼쪽으로 완만하게 휘어 이동",
    "curve_right": "오른쪽으로 완만하게 휘어 이동",
    "turn_left": "왼쪽으로 방향을 바꾸는 지점에서 이동",
    "turn_right": "오른쪽으로 방향을 바꾸는 지점에서 이동",
    "turn_sharp_left": "왼쪽으로 크게 방향을 바꾸는 지점에서 이동",
    "turn_sharp_right": "오른쪽으로 크게 방향을 바꾸는 지점에서 이동",
    "turn_reverse": "진행 방향을 반대로 바꾸는 지점에서 이동",
    "direction_left": "왼쪽으로 진행 방향이 바뀌는 부분에서 이동",
    "direction_right": "오른쪽으로 진행 방향이 바뀌는 부분에서 이동",
    "local_stay": "좁은 범위 안에서 위치가 모인 상태",
}


def require_action(request):
    action = request.get("action")
    if (
        not isinstance(action, dict)
        or not action.get("id")
        or action.get("kind") not in ACTION_MEANINGS
        or action.get("material") != {"무엇을": ACTION_MEANINGS[action["kind"]]}
        or not isinstance(action.get("actor"), dict)
    ):
        raise ValueError("behavior pin required for action writing")
    return action


def require_scene_action(scene, request):
    action = require_action(request)
    record = scene.user_record
    if (
        record is None
        or record.kind != "behavior"
        or request.get("card_id") != scene.id
        or datetime.fromisoformat(request["event_at"]) != scene.anchor.event_at
        or action["id"] != "action:" + digest(scene.core)
        or action["kind"] != record.code
        or action["actor"].get("id") != record.pet_id
    ):
        raise ValueError("action writing is not bound to this behavior pin")


def pin_movement(items):
    """Retain only the supported phase containing the pin, without neighboring events."""
    selected = []
    for item in items:
        facts = item["facts"]
        at = facts["scene_at_s"]
        claims = {c["id"]: c for c in facts["claims"]}
        phases = []
        for phase in facts["phases"]:
            if not phase["start_s"] <= at < phase["end_s"]:
                continue
            refs = [r for r in phase["claims"] if claims[r].get("event_s", at) == at]
            if refs:
                phases.append({**phase, "claims": refs})
        if phases:
            refs = {r for p in phases for r in p["claims"]}
            selected.append(
                {
                    **item,
                    "facts": {
                        **facts,
                        "phases": phases,
                        "claims": [c for c in facts["claims"] if c["id"] in refs],
                    },
                }
            )
    # A pin cannot establish two different simultaneous phases. Keep the action alone.
    return selected if sum(len(x["facts"]["phases"]) for x in selected) == 1 else []


def context_uses(request):
    from daengs_walk.diary.board.activity import movement_uses

    require_action(request)
    uses = movement_uses(request)
    current = [u for u in uses if u["from_s"] <= 0 < u["to_s"] and u.get("at_s", 0) == 0]
    return current if len({(u["from_s"], u["to_s"]) for u in current}) <= 1 else []


def project_action(request):
    action = require_action(request)
    payload = {
        "recorded_action": {
            "id": "a1",
            "actor": action["actor"].get("name"),
            "action": action["material"]["무엇을"],
        }
    }
    refs = {"a1": action["id"]}
    uses = context_uses(request)
    kinds = {u["meaning"] for u in uses}
    shape_keys = kinds & SHAPE_TEXT.keys()
    if len(shape_keys) > 1:
        shape_keys.discard("straight_run")
    pace = kinds & {"relative_slow", "relative_fast"}
    if len(shape_keys) > 1 or len(pace) > 1:
        return payload, refs
    selected_kinds = shape_keys | pace | (kinds & {"retrace"})
    if not selected_kinds:
        return payload, refs
    meaning = SHAPE_TEXT[next(iter(shape_keys))] if shape_keys else "이동"
    if "retrace" in kinds:
        meaning = "앞서 지나온 구간을 되짚으며 " + meaning
    if pace:
        pace_text = "느린" if "relative_slow" in pace else "빠른"
        meaning += f". 그 시각의 걸음은 이번 산책의 다른 이동 구간보다 상대적으로 {pace_text} 걸음"
    payload["movement_context"] = {
        "id": "m1",
        "meaning": meaning,
        "relation": "행동이 기록된 시각의 산책 이동. 행동의 지속 구간이나 원인은 아님",
    }
    refs["m1"] = sorted(u["id"] for u in uses if u["meaning"] in selected_kinds)
    return payload, refs
