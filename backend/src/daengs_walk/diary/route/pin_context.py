"""Verified movement phases at one pin; no writing or neighboring-action policy."""

from daengs_walk.diary.route.patterns import PATTERN_CASES
from daengs_walk.value_contracts import digest

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


MEANINGS = {
    **{key: next(iter(value.values())) for key, value in PATTERN_CASES.items()},
    **{key: value for key, value in SHAPE_TEXT.items() if key not in PATTERN_CASES},
    "relative_slow": "이번 산책의 기준 속도보다 상대적으로 느린 이동",
    "relative_fast": "이번 산책의 기준 속도보다 상대적으로 빠른 이동",
}


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
