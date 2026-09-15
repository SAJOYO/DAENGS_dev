"""Extract whole canonical segments, preserving endpoint binding and coverage gaps."""

from math import fsum, isclose, isfinite

from daengs_walk.diary.relational.comparison import aware_time


def extract_journey(previous, current, evidence, revision):
    if previous is None or evidence is None:
        return None
    if (
        previous.get("walk_session")
        and current.get("walk_session")
        and previous["walk_session"] != current["walk_session"]
    ):
        return None
    start, end = (aware_time(f["anchor"].get("event_at")) for f in (previous, current))
    if start is None or end is None or start >= end:
        return None

    def binding(frame):
        anchor = frame["anchor"]
        refs = anchor.get("source_fixes", [])
        if (
            anchor.get("method") != "observed"
            or anchor.get("position_state") != "resolved"
            or len(refs) != 1
        ):
            return None
        ref = refs[0]
        points = [
            p
            for p in evidence.accepted_points
            if p.client_seq == ref.get("client_seq")
            and p.chain_index == ref.get("chain_index")
            and p.at == aware_time(anchor["event_at"])
            and p.at == aware_time(ref.get("at"))
            and anchor.get("point") == {"lat": p.lat, "lng": p.lng}
            and ("location_at" not in anchor or aware_time(anchor["location_at"]) == p.at)
        ]
        return (points[0].chain_index, points[0].client_seq) if len(points) == 1 else None

    left, right = binding(previous), binding(current)
    segments = sorted(
        (s for s in evidence.segments if start <= s.a.at < s.b.at <= end),
        key=lambda s: (s.a.at, s.b.at, s.chain_index, s.a.client_seq),
    )
    cursor, uncovered, links, previous_fix, seen = start, [], [], None, set()
    connected = left is not None and right is not None
    for s in segments:
        key = (s.chain_index, s.a.client_seq, s.b.client_seq)
        if key in seen or s.a.at < cursor:
            raise ValueError("duplicate or overlapping canonical journey segments")
        seen.add(key)
        if (
            not all(isfinite(v) and v >= 0 for v in (s.dt, s.dist))
            or not isclose(s.dt, (s.b.at - s.a.at).total_seconds(), abs_tol=1e-6)
            or s.a.chain_index != s.chain_index
            or s.b.chain_index != s.chain_index
        ):
            raise ValueError("invalid canonical journey segment")
        first = (s.chain_index, s.a.client_seq)
        if not links and first != left:
            connected = False
        if s.a.at > cursor:
            uncovered.append({"start": cursor.isoformat(), "end": s.a.at.isoformat()})
        if previous_fix is not None and previous_fix != first:
            connected = False
        cursor = s.b.at
        previous_fix = (s.chain_index, s.b.client_seq)
        links.append({"chain": s.chain_index, "from_seq": s.a.client_seq, "to_seq": s.b.client_seq})
    if cursor < end:
        uncovered.append({"start": cursor.isoformat(), "end": end.isoformat()})
    connected = connected and previous_fix == right
    return {
        "status": "connected" if connected and segments and not uncovered else "partial",
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "elapsed_seconds": (end - start).total_seconds(),
        "observed_seconds": fsum(s.dt for s in segments),
        "observed_distance_m": round(fsum(s.dist for s in segments), 1),
        "moving_distance_m": round(fsum(s.dist for s in segments if s.moving), 1),
        "uncovered_intervals": uncovered,
        "source_revision": revision,
        "segments": links,
        "endpoint_binding": {"previous": left is not None, "current": right is not None},
    }


def writing_journey(journey, previous, current):
    def endpoint(frame):
        road = frame.get("road_reference")
        return {
            "recorded_at": frame["anchor"]["event_at"],
            "space": [
                {
                    "value": m["material"],
                    "scope": m["relation"],
                    "source_time": m.get("time_meaning"),
                }
                for m in frame["space"].get("materials", [])
                if m["role"] in {"point_land_cover", "location_label"}
            ],
            "road": {"name": road["road_nm"], "scope": road["scope"]} if road else None,
        }

    return {
        "id": "journey",
        "subject": "산책을 기록한 기기의 이동",
        "connection": journey["status"],
        "elapsed_seconds": journey["elapsed_seconds"],
        "observed_seconds": journey["observed_seconds"],
        "observed_distance_m": journey["observed_distance_m"],
        "moving_distance_m": journey["moving_distance_m"],
        "uncovered_intervals": journey["uncovered_intervals"],
        "previous_record": endpoint(previous),
        "current_record": endpoint(current),
        "scope": "공간 자료는 양 끝 기록점에만 적용. 중간 피복·특정 도로 통과·공간 경계 진입은 미확인",
    }
