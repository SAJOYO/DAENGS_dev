"""Original GEO synthetic geometry inputs, adapted to DEV point contracts."""

import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from daengs_walk.contracts import WalkEvidencePoint
from daengs_walk.diary_route_normalize import RoutePatternSource

START = datetime(2026, 9, 8, 6, tzinfo=UTC)
ORIGIN = (37.0, 130.0)  # Synthetic geometry, not a real person's walk.
LABELS = {
    "local_stay": "국소 체류",
    "straight_run": "직선 이동",
    "turn": "방향 전환",
    "retrace": "되짚기",
}


def route(name, waypoints, *, noise_m=0, step_s=5):
    """Waypoints are (elapsed seconds, east metres, north metres)."""
    samples = []
    for k, (a, b) in enumerate(pairwise(waypoints)):
        times = list(range(a[0], b[0], step_s))
        if k == len(waypoints) - 2:
            times.append(b[0])
        for t in times:
            ratio = (t - a[0]) / (b[0] - a[0])
            x, y = a[1] + ratio * (b[1] - a[1]), a[2] + ratio * (b[2] - a[2])
            x += noise_m * math.sin(t * 1.71)
            y += noise_m * math.cos(t * 1.19)
            samples.append(
                WalkEvidencePoint(
                    client_seq=len(samples),
                    at=START + timedelta(seconds=t),
                    lat=ORIGIN[0] + math.degrees(y / 6_371_000),
                    lng=ORIGIN[1]
                    + math.degrees(x / (6_371_000 * math.cos(math.radians(ORIGIN[0])))),
                    accuracy_m=5,
                    is_mock=True,
                )
            )
    return RoutePatternSource(
        session_id=name, started_at=START, ended_at=samples[-1].at, points=tuple(samples)
    )


def scenarios():
    routes = {
        "straight": ("직선 + 작은 흔들림", [(0, 0, 0), (120, 120, 0)], 2),
        "right": ("뚜렷한 우회전", [(0, 0, 0), (90, 0, 90), (180, 90, 90)], 1),
        "left": ("뚜렷한 좌회전", [(0, 0, 0), (90, 0, 90), (180, -90, 90)], 1),
        "stay": ("제자리 관측 + 흔들림", [(0, 0, 0), (90, 0, 0)], 2),
        "pause_turn": ("머문 뒤 우회전", [(0, 0, 0), (90, 0, 90), (150, 0, 90), (240, 90, 90)], 1),
        "out_back": ("같은 구간 되짚기", [(0, 0, 0), (120, 0, 120), (200, 0, 40)], 1),
        "parallel": (
            "다른 쪽으로 돌아오기",
            [(0, 0, 0), (120, 0, 120), (155, 35, 120), (275, 35, 0)],
            0,
        ),
        "shallow": ("완만한 방향 변화", [(0, 0, 0), (90, 0, 90), (180, 24, 180)], 0),
        "short": ("짧은 꺾임", [(0, 0, 0), (15, 0, 15), (30, 15, 15)], 0),
    }
    result = {
        name: {"label": label, "source": route(name, points, noise_m=noise)}
        for name, (label, points, noise) in routes.items()
    }
    gap = route("gap", [(0, 0, 0), (90, 0, 90), (210, 0, 90), (300, 90, 90)])
    gap = gap.model_copy(
        update={
            "points": tuple(p for p in gap.points if not 90 < (p.at - START).total_seconds() < 210)
        }
    )
    result["gap"] = {"label": "모서리에서 GPS 공백", "source": gap}
    return result
