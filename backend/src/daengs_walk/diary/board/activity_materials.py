"""Card-scoped semantic composition. Numeric spans and source citations stay internal."""

from itertools import groupby

PATH_TEXT = {
    "straight_run": "대체로 곧게 이동",
    "curve_left": "왼쪽으로 완만하게 휘어 이동",
    "curve_right": "오른쪽으로 완만하게 휘어 이동",
    "local_stay": "좁은 범위 안에 위치가 모이고 바깥으로의 진행이 제한됨",
    "turn_left": "왼쪽으로 방향을 꺾음",
    "turn_right": "오른쪽으로 방향을 꺾음",
    "turn_sharp_left": "왼쪽으로 크게 방향을 꺾음",
    "turn_sharp_right": "오른쪽으로 크게 방향을 꺾음",
    "turn_reverse": "진행 방향을 반대로 바꿈",
    "direction_left": "왼쪽으로 진행 방향이 바뀜",
    "direction_right": "오른쪽으로 진행 방향이 바뀜",
}


def _meaning(paths):
    # Retracing is a relation, so it coexists with local shape.
    shape = [PATH_TEXT[k] for k in sorted(paths) if k in PATH_TEXT]
    if "retrace" in paths:
        return "앞서 지나온 구간을 역순으로 따라가며 " + (", ".join(shape) if shape else "이동")
    return ", ".join(shape) if shape else "이동"


def _scope(left, right, start, end):
    if left == start and right == end:
        return "이 이동 전체"
    if left == start:
        return "이 이동의 시작 부분"
    if right == end:
        return "이 이동의 끝부분"
    return "이 이동 중간의 일부 구간"


def compose_activity(uses, action):
    """Fold unchanged flow, attach locally bound pace, preserve distinct point events."""
    refs, materials, connections = {}, [], []

    def reference(items):
        key = f"m{len(refs) + 1}"
        refs[key] = sorted({u["id"] for u in items})
        return key

    phases = []
    ordered = sorted(uses, key=lambda u: (u["from_s"], u["to_s"], u["id"]))
    for span, group in groupby(ordered, lambda u: (u["from_s"], u["to_s"])):
        items = list(group)
        paths = frozenset(u["meaning"] for u in items if u["kind"] == "path" and "at_s" not in u)
        events = [u for u in items if "at_s" in u]
        continuous = [u for u in items if "at_s" not in u]
        if continuous:
            phases.append(
                {
                    "start": span[0],
                    "end": span[1],
                    "paths": paths,
                    "uses": continuous,
                    "sources": frozenset(u["source_id"] for u in continuous if u["kind"] == "path"),
                }
            )
        for event in events:
            # Event pace is checked at the pivot, not over its supporting legs.
            paces = [
                u for u in items if u["kind"] == "pace" and u["from_s"] <= event["at_s"] < u["to_s"]
            ]
            label = _meaning({event["meaning"]})
            if paces:
                label += (
                    " (그 전환 시점의 이동은 "
                    + (
                        "상대적으로 느림"
                        if paces[0]["meaning"] == "relative_slow"
                        else "상대적으로 빠름"
                    )
                    + ")"
                )
            ref = reference([event, *paces])
            materials.append(
                (event["at_s"], {"id": ref, "meaning": label, "occurrence": "한 번의 방향 전환"})
            )
            if event["at_s"] == 0:
                connections.append({"movement_id": ref, "relation": "이 전환의 기록 시각"})

    flows = []
    event_times = {u["at_s"] for u in uses if "at_s" in u}
    for phase in phases:
        if (
            flows
            and flows[-1][-1]["end"] == phase["start"]
            and flows[-1][0]["paths"] == phase["paths"]
            and flows[-1][0]["sources"] == phase["sources"]
            and phase["start"] not in event_times
        ):
            flows[-1].append(phase)
        else:
            flows.append([phase])
    for flow in flows:
        start, end = flow[0]["start"], flow[-1]["end"]
        all_uses = [u for p in flow for u in p["uses"]]
        paths = [u for u in all_uses if u["kind"] == "path"]
        paces = [u for u in all_uses if u["kind"] == "pace"]
        if not paths and not paces:
            continue
        value = {"meaning": _meaning(flow[0]["paths"])}
        if paths:
            value["id"] = reference(paths)
            source_start = max(
                min(u["support_from_s"] for u in paths if u["meaning"] == kind)
                for kind in flow[0]["paths"]
            )
            source_end = min(
                max(u["support_to_s"] for u in paths if u["meaning"] == kind)
                for kind in flow[0]["paths"]
            )
            value["extent"] = _scope(start, end, source_start, source_end).replace(
                "이 이동", "원래 이동"
            )
        changes = []
        for pace in sorted(paces, key=lambda u: (u["from_s"], u["to_s"], u["id"])):
            if (
                changes
                and changes[-1]["end"] == pace["from_s"]
                and changes[-1]["meaning"] == pace["meaning"]
            ):
                changes[-1]["end"] = pace["to_s"]
                changes[-1]["uses"].append(pace)
            else:
                changes.append(
                    {
                        "start": pace["from_s"],
                        "end": pace["to_s"],
                        "meaning": pace["meaning"],
                        "uses": [pace],
                    }
                )
        value["changes"] = []
        for change in changes:
            qualifier = "느리게" if change["meaning"] == "relative_slow" else "빠르게"
            overlapping = [
                u for u in paths if u["from_s"] < change["end"] and change["start"] < u["to_s"]
            ]
            ref = reference([*overlapping, *change["uses"]])
            value["changes"].append(
                {
                    "id": ref,
                    "scope": _scope(change["start"], change["end"], start, end),
                    "meaning": "위 이동을 이번 산책의 기준보다 상대적으로 " + qualifier + " 이어감",
                }
            )
            if change["start"] <= 0 < change["end"]:
                connections.append(
                    {"movement_id": ref, "relation": "이 속도가 적용되는 부분의 기록 시각"}
                )
        if not paths:
            # No shape was established. Still keep the supported pace, without inventing straight.
            value["meaning"] = "이동"
        if paths and start <= 0 < end and not any(c["start"] <= 0 < c["end"] for c in changes):
            connections.append({"movement_id": value["id"], "relation": "이 이동 구간의 기록 시각"})
        materials.append((start, value))
    payload = {
        "movement": {
            "materials": [v for _, v in sorted(materials, key=lambda p: (p[0], p[1].get("id", "")))]
        }
    }
    # Aliases follow narrative order, even though events were detected before flows.
    entries = [
        entry
        for material in payload["movement"]["materials"]
        for entry in [material, *material.get("changes", [])]
        if "id" in entry
    ]
    aliases = {entry["id"]: f"m{i}" for i, entry in enumerate(entries, 1)}
    refs = {aliases[key]: value for key, value in refs.items()}
    for entry in entries:
        entry["id"] = aliases[entry["id"]]
    for connection in connections:
        connection["movement_id"] = aliases[connection["movement_id"]]
    if action:
        refs["a1"] = action["id"]
        payload["recorded_action"] = {
            "id": "a1",
            "actor": action["actor"].get("name"),
            "action": action["material"]["무엇을"],
            "connections": connections,
        }
    return payload, refs
