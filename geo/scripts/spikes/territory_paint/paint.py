"""스파이크: 산책 점을 붓으로 써서 지도를 칠한다 — 실제 도로망 위 24회 산책으로.

    uv run python -m scripts.spikes.territory_paint.real_route --json walks.json      # 먼저 경로를 만든다
    uv run python -m scripts.spikes.territory_paint.paint --walks walks.json
    uv run python -m scripts.spikes.territory_paint.paint --walks walks.json --scenes paint.json

## 무엇을 묻나

붓 반경(`brush_m`)과 저장 격자(`cell_radius`) 두 값이 그림을 정한다. 고를 근거가 필요하다.

    자주 가는 영역이 실제로 구별되나   — 늘 가는 길과 가끔 가는 길이 밝기로 갈리나
    저장이 감당되나                   — 한 달에 셀 몇 칸인가
    집이 얼마나 튀나                  — 모든 산책이 현관에서 시작하니 반드시 튄다

마지막 항목이 이 설계의 진짜 위험이다. 칠한 지도는 예쁘지만 **가장 밝은 칸이 집이다.**
숫자로 잡아 두고 시작한다(`home_bias`).

궤적은 실제 OSM 보행 도로망(대치·도곡동 + 양재천) 위에서 만든 것이고, GPS 지터만 합성이다.
"""

import argparse
import json
import math
import random
import sys
from datetime import UTC, datetime, timedelta

from app.features.territory.paint import (
    FACILITY_SMOOTH,
    FACILITY_STEP,
    NARROW_SMOOTH,
    NARROW_STEP,
    canvas_stats,
    flat,
    paint_sheet,
    shift_times,
    stack,
)
from app.features.walk.facts import compute_facts
from app.features.walk.models import WalkFix
from app.geo.cells import hex_center_latlng

EARTH_R = 6_371_000.0
WALK_SPEED_MPS = 1.2
JITTER_M = 8.0
BASE = datetime(2026, 7, 1, tzinfo=UTC)

CELL_RADII = (8.0, 15.0)   # 격자 단위. 위도 37.5°에서 실제 6.3m · 11.9m
PROFILES = (flat(25.0), NARROW_STEP, NARROW_SMOOTH, FACILITY_STEP, FACILITY_SMOOTH)


def densify(path: list, rng: random.Random) -> list[WalkFix]:
    """노드 경로 → 1Hz fix 열. 보행 속도로 채우고 GPS 지터를 얹는다."""
    fixes: list[WalkFix] = []
    t = 0.0
    for index in range(len(path) - 1):
        (lat1, lng1), (lat2, lng2) = path[index], path[index + 1]
        mid = math.radians((lat1 + lat2) / 2)
        dx = math.radians(lng2 - lng1) * EARTH_R * math.cos(mid)
        dy = math.radians(lat2 - lat1) * EARTH_R
        leg = math.hypot(dx, dy)
        count = max(1, int(leg / WALK_SPEED_MPS))
        for k in range(count):
            frac = k / count
            lat = lat1 + (lat2 - lat1) * frac
            lng = lng1 + (lng2 - lng1) * frac
            jlat = lat + math.degrees(rng.gauss(0, JITTER_M) / EARTH_R)
            jlng = lng + math.degrees(
                rng.gauss(0, JITTER_M) / (EARTH_R * math.cos(math.radians(lat)))
            )
            fixes.append(WalkFix(client_seq=len(fixes), chain_index=0,
                                 at=BASE + timedelta(seconds=t), lat=jlat, lng=jlng,
                                 accuracy_m=JITTER_M, is_mock=False))
            t += 1.0
    return fixes


def segments_for(path: list, rng: random.Random):
    fixes = densify(path, rng)
    if len(fixes) < 2:
        return []
    ended = fixes[-1].at + timedelta(seconds=1)
    return compute_facts("paint", "spike", BASE, ended, fixes).segments


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--walks", required=True, help="real_route 가 만든 JSON")
    parser.add_argument("--scenes", help="눈으로 볼 장면을 쓸 JSON 경로")
    args = parser.parse_args(argv)

    with open(args.walks, encoding="utf-8") as handle:
        source = json.load(handle)
    paths = [[tuple(p) for p in w] for w in source["walks"]]
    home = tuple(source["home"])
    rng = random.Random(11)

    tracks = [segments_for(path, rng) for path in paths]
    tracks = [t for t in tracks if t]
    print(f"산책 {len(tracks)}회 · 세그먼트 {sum(len(t) for t in tracks)}개 "
          f"· 총 {sum(source['lengths_m']) / 1000:.1f}km · GPS 지터 σ={JITTER_M:.0f}m")

    scenes = {}
    print(f"\n  {'셀':>4} {'프로파일':<14} │ {'칸수':>5} {'넓이':>8} {'핵심':>5} "
          f"{'심안':>6} {'가장자리':>7} {'집튐':>6}")
    for cell_radius in CELL_RADII:
        for profile in PROFILES:
            sheets = [
                paint_sheet(f"w{d}", shift_times(BASE, d), t, cell_radius, profile)
                for d, t in enumerate(tracks)
            ]
            canvas = stack(sheets)
            stats = canvas_stats(canvas, cell_radius, len(tracks))
            print(f"  {cell_radius:3.0f}m {profile.name:<14} │ {stats.cells:5} "
                  f"{stats.area_m2 / 10000:6.1f}ha {stats.core_cells:5} "
                  f"{stats.core_hit_cells:6} {stats.fringe_cells:7} {stats.home_bias:5.1f}배")
            top = max(p.occupancy for p in canvas.values())
            scenes[f"{cell_radius:.0f}|{profile.name}"] = {
                "cell_radius": cell_radius, "profile": profile.name,
                "bands": list(profile.bands), "smooth": profile.smooth,
                "cells": stats.cells, "area_ha": round(stats.area_m2 / 10000, 1),
                "core": stats.core_cells, "core_hit": stats.core_hit_cells,
                "fringe": stats.fringe_cells, "home_bias": round(stats.home_bias, 2),
                "max_walks": stats.max_walks,
                # 필드 표시용: 중심 좌표 + 세 값. 육각 테두리는 안 보낸다 —
                # 연속 장으로 그릴 것이라 칸 경계가 필요 없다.
                "field": [
                    [round(la, 6), round(ln, 6), round(p.occupancy / top, 4),
                     p.walks, round(p.peak, 3)]
                    for p, (la, ln) in (
                        (p, hex_center_latlng(*p.cell, cell_radius)) for p in canvas.values()
                    )
                ],
            }

    # 기준 조합 하나로 "가장 진한 곳이 집인가" 를 잰다. 조합을 바꿔도 결론은 안 바뀐다.
    reference = stack([
        paint_sheet(f"w{d}", shift_times(BASE, d), t, 15.0, NARROW_STEP)
        for d, t in enumerate(tracks)
    ])
    hottest = max(reference.values(), key=lambda p: p.occupancy)
    home_dist = _dist(hex_center_latlng(*hottest.cell, 15.0), home)
    print(f"\n물감이 가장 많은 칸과 집의 거리: {home_dist:.0f}m "
          f"— 0 에 가까울수록 '가장 진한 곳 = 집' 이다")

    if args.scenes:
        payload = {
            "home": list(home),
            "walks": [[[round(p[0], 6), round(p[1], 6)] for p in w] for w in paths],
            "lengths_m": source["lengths_m"],
            "combos": scenes,
            "cell_radii": [int(r) for r in CELL_RADII],
            "profiles": [p.name for p in PROFILES],
            "home_dist_m": round(home_dist),
        }
        with open(args.scenes, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        print(f"\n조합 {len(scenes)}개 → {args.scenes}")
    return 0


def _dist(a, b) -> float:
    mid = math.radians((a[0] + b[0]) / 2)
    return math.hypot(
        math.radians(b[1] - a[1]) * EARTH_R * math.cos(mid),
        math.radians(b[0] - a[0]) * EARTH_R,
    )


if __name__ == "__main__":
    sys.exit(main())
