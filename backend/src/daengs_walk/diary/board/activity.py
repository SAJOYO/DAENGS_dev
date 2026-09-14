"""Small, phase-specific movement claims shared by wire and result validation."""

from datetime import datetime

from daengs_walk.diary.board.activity_materials import PATH_TEXT, compose_activity
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.route.patterns import PATTERN_CASES

MEANINGS = {
    **{key: next(iter(value.values())) for key, value in PATTERN_CASES.items()},
    **{key: value for key, value in PATH_TEXT.items() if key not in PATTERN_CASES},
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
    return compose_activity(movement_uses(request), request.get("action"))


def require_activity_transfer(request, payload, references):
    """Invocation guard: admission and actual request must cover the same claims."""
    expected, refs = activity_projection(request)
    if payload != expected or references != refs:
        raise ValueError("selected movement lost at model boundary")


def activity_fallback(request):
    uses = movement_uses(request)
    # Fallback describes one nearest phase, without merging pace across its boundaries.
    if uses:
        spans = {(u["from_s"], u["to_s"]) for u in uses}
        span = min(spans, key=lambda s: (max(s[0], -s[1], 0), s))
        chosen = [u for u in uses if (u["from_s"], u["to_s"]) == span]
        order = {
            "retrace": 0,
            "turn_reverse": 1,
            "turn_left": 2,
            "turn_right": 2,
            "local_stay": 3,
            "straight_run": 4,
        }
        paths = sorted(
            (u for u in chosen if u["kind"] == "path"), key=lambda u: order.get(u["meaning"], 2)
        )
        paces = [u for u in chosen if u["kind"] == "pace"]
        selected = paths[:1] + paces[:1]
        when = (
            "이 기록에 앞선 구간"
            if span[1] <= 0
            else ("이 기록 이후 구간" if span[0] >= 0 else "이 기록 무렵의 구간")
        )
        text = (
            when + "에서는 " + ", ".join(MEANINGS[u["meaning"]] for u in selected) + "이 관측됐다."
        )
    else:
        selected, text = [], ""
    action = request.get("action")
    if action:
        name = action["actor"].get("name")
        text += (
            ("\n" if text else "")
            + (f"{name}의 " if name else "")
            + action["material"]["무엇을"]
            + " 행동을 기록했다."
        )
    return text, tuple(u["id"] for u in selected)


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
