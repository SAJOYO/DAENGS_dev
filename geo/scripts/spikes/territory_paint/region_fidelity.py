"""스파이크: 셀 방문 기록으로 면 체류를 근사할 수 있나 — 반지름은 몇이어야 하나.

    uv run python -m scripts.spikes.territory_paint.region_fidelity
    uv run python -m scripts.spikes.territory_paint.region_fidelity --trials 60 --csv out.csv

## 무엇을 묻나

결정 #57 이 원좌표를 finish 직후 purge 한다. 그래서 **나중에 그린 면**으로 과거 산책을
판정하려면 좌표가 아닌 무언가가 남아 있어야 하고, 후보가 셀 방문 기록이다
(`app/features/territory/region.py`). 셀은 손실 압축이라 답이 틀린다. 문제는 "틀리나"가 아니라
**"이미 있는 GPS 지터보다 더 틀리나"** 다. 덜 틀리면 양자화는 공짜다.

그래서 셋을 같은 산책에 대해 잰다.

    truth    지터 없는 궤적 × 폴리곤 정밀 교차      — 참값
    exact    지터 있는 fix → segments × 폴리곤      — 지금 구조가 낼 수 있는 최선
    approx   지터 있는 fix → 셀 방문 × 폴리곤       — purge 뒤에도 가능한 값

`exact` 오차가 GPS 가 이미 지불하는 값이고, `approx − exact` 가 셀을 도입해서 **추가로**
내는 값이다. 후자가 전자보다 작으면 셀 층은 정확도를 실질적으로 안 깎는다.

## 합성인 이유

실제 export 2건은 에뮬레이터 고정점(15 fix)이라 면 진입이 없다. 참값을 알아야 오차를
잴 수 있는데 실기기 데이터에는 참값이 없다 — 어디까지가 공원 안이었는지 아무도 모른다.
그래서 참값을 아는 궤적을 만든다. **문턱값을 정하는 근거가 아니라 자릿수를 보는 도구다** —
실기기 반복 보행이 이 자리를 대체해야 한다.
"""

import argparse
import csv
import json
import math
import random
import statistics
import sys
from datetime import UTC, datetime, timedelta

from app.features.territory.region import (
    Region,
    _point_in_ring,
    _projector,
    cell_visits,
    dwell_by_region,
    region_dwell_from_cells,
    region_encounters,
)
from app.features.walk.facts import compute_facts
from app.features.walk.models import WalkFix
from app.geo.cells import (
    cell_id,
    cell_size_m,
    hex_boundary_latlng,
    hex_center_latlng,
    hex_sample_points,
    inverse_mercator,
)

EARTH_R = 6_371_000.0
ORIGIN_LAT, ORIGIN_LNG = 37.4979, 127.0276      # 강남역 — 기존 픽스처와 같은 자리

WALK_SPEED_MPS = 1.2                             # 개와 걷는 속도. 사람 단독 1.4 보다 느리다
SAMPLE_HZ = 1.0
STARTED = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)

# 재는 축 둘. 공원 크기는 한 변(m), 지터는 수평 오차 표준편차(m).
PARK_SIDES = (50.0, 150.0, 400.0)
JITTERS = (5.0, 12.0)
# 격자 **단위**. 위도 37.5° 에서 실제 22.2 · 39.7 · 91.2 · 198.3m.
RADII = (28.0, 50.0, 115.0, 250.0)


def to_latlng(x: float, y: float) -> tuple[float, float]:
    """원점 기준 미터 → 위경도. 등장방형 — region.py 와 같은 투영이다."""
    lat = ORIGIN_LAT + math.degrees(y / EARTH_R)
    lng = ORIGIN_LNG + math.degrees(x / (EARTH_R * math.cos(math.radians(ORIGIN_LAT))))
    return lat, lng


def square_region(side: float, region_id: str) -> Region:
    """원점을 중심에 둔 정사각 면. 사용자가 손으로 그린 공원 대역이라고 본다."""
    half = side / 2
    corners = ((-half, -half), (half, -half), (half, half), (-half, half))
    return Region(id=region_id, version=1, ring=tuple(to_latlng(x, y) for x, y in corners))


def route_points(side: float) -> list[tuple[float, float]]:
    """면을 가로질러 들어갔다가 안에서 한 바퀴 돌고 나오는 경로(미터).

    바깥에서 시작해 바깥에서 끝난다 — 진입·이탈이 둘 다 관측돼야 occurrence 가
    잘리지 않고, 참값도 온전히 정의된다.
    """
    half = side / 2
    inner = half * 0.55
    return [
        (-half * 3, -half * 1.4),        # 바깥 접근
        (-half * 0.9, -half * 0.9),
        (-inner, -inner),                # 진입
        (inner, -inner),                 # 안에서 한 바퀴
        (inner, inner),
        (-inner, inner),
        (-inner, -inner * 0.2),
        (half * 1.1, half * 0.6),        # 이탈
        (half * 2.6, half * 1.6),        # 바깥 이탈 후
    ]


def walk_track(points: list[tuple[float, float]], dwell_at: int = 3,
               dwell_s: float = 40.0) -> list[tuple[float, float, float]]:
    """경유점 → 1Hz 로 샘플한 (t, x, y). 지터 없는 참 궤적이다.

    `dwell_at` 번째 경유점에서 `dwell_s` 만큼 멈춘다 — 산책에는 정지가 있고, 정지는
    체류의 대부분을 만든다. 정지 없는 궤적으로 재면 셀 근사가 실제보다 잘 나온다.
    """
    track: list[tuple[float, float, float]] = []
    t = 0.0
    step = 1.0 / SAMPLE_HZ
    for index in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[index], points[index + 1]
        leg = math.hypot(x2 - x1, y2 - y1)
        count = max(1, int(leg / (WALK_SPEED_MPS * step)))
        for k in range(count):
            frac = k / count
            track.append((t, x1 + (x2 - x1) * frac, y1 + (y2 - y1) * frac))
            t += step
        if index + 1 == dwell_at:
            held = 0.0
            while held < dwell_s:
                track.append((t, x2, y2))
                t += step
                held += step
    track.append((t, *points[-1]))
    return track


def to_fixes(track, rng: random.Random, jitter_m: float) -> list[WalkFix]:
    """참 궤적 → 기기가 봤을 fix. 수평 오차는 등방 가우시안으로 넣는다."""
    fixes = []
    for seq, (t, x, y) in enumerate(track):
        dx = rng.gauss(0.0, jitter_m)
        dy = rng.gauss(0.0, jitter_m)
        lat, lng = to_latlng(x + dx, y + dy)
        fixes.append(
            WalkFix(
                client_seq=seq,
                chain_index=0,
                at=STARTED + timedelta(seconds=t),
                lat=lat,
                lng=lng,
                # 보고 정확도는 실제 오차를 그대로 알려주지 않는다. 관대하게 준다 —
                # accuracy 로 거르는 필터(MAX_ACCURACY_M=50)가 지터를 대신 지우면
                # 이 실험이 재려는 것이 사라진다.
                accuracy_m=max(3.0, jitter_m),
                is_mock=False,
            )
        )
    return fixes


def truth_dwell(track, region: Region) -> float:
    """지터 없는 궤적의 정밀 체류. 참값 — 같은 기하 함수를 쓰되 입력만 깨끗하다."""
    clean = [
        WalkFix(client_seq=i, chain_index=0, at=STARTED + timedelta(seconds=t),
                lat=to_latlng(x, y)[0], lng=to_latlng(x, y)[1], accuracy_m=3.0, is_mock=False)
        for i, (t, x, y) in enumerate(track)
    ]
    ended = clean[-1].at + timedelta(seconds=1)
    computed = compute_facts("truth", "spike", STARTED, ended, clean)
    return dwell_by_region(region_encounters(computed.segments, [region])).get(region.id, 0.0)


def one_trial(side: float, jitter: float, seed: int) -> dict:
    rng = random.Random(seed)
    region = square_region(side, f"park-{int(side)}")
    track = walk_track(route_points(side))
    truth = truth_dwell(track, region)

    fixes = to_fixes(track, rng, jitter)
    ended = fixes[-1].at + timedelta(seconds=1)
    computed = compute_facts("trial", "spike", STARTED, ended, fixes)
    exact = dwell_by_region(region_encounters(computed.segments, [region])).get(region.id, 0.0)

    row = {"side": side, "jitter": jitter, "seed": seed, "truth": truth, "exact": exact,
           "segments": len(computed.segments)}
    for radius in RADII:
        visits = cell_visits(computed.segments, radius)
        row[f"w{int(radius)}"] = region_dwell_from_cells(visits, region, radius, weighted=True)
        row[f"c{int(radius)}"] = region_dwell_from_cells(visits, region, radius, weighted=False)
        row[f"n{int(radius)}"] = len(visits)
    return row


def summarise(rows: list[dict]) -> None:
    def err(rows_, key, ref="truth"):
        vals = [abs(r[key] - r[ref]) for r in rows_ if r[ref] > 0]
        return statistics.median(vals) if vals else float("nan")

    for side in PARK_SIDES:
        for jitter in JITTERS:
            group = [r for r in rows if r["side"] == side and r["jitter"] == jitter]
            if not group:
                continue
            truth = statistics.median(r["truth"] for r in group)
            exact_err = err(group, "exact")
            print(f"\n■ 공원 한 변 {side:.0f}m ({side * side / 10000:.1f}ha) · "
                  f"GPS 지터 σ={jitter:.0f}m · 시행 {len(group)}회")
            print(f"  참 체류 {truth:6.1f}s · exact 오차(중앙) {exact_err:5.1f}s "
                  f"({exact_err / truth * 100:4.1f}%)  ← GPS 가 이미 내는 값")
            print(f"  {'셀(실제)':>7} {'셀수':>4} │ {'면적가중':>18} │ {'중심판정':>18}")
            for radius in RADII:
                cells = statistics.median(r[f"n{int(radius)}"] for r in group)
                we, ce = err(group, f"w{int(radius)}"), err(group, f"c{int(radius)}")
                # 셀이 추가로 내는 값 = 전체 오차 − GPS 가 이미 내던 값
                w_add, c_add = we - exact_err, ce - exact_err
                print(f"  {cell_size_m(radius, ORIGIN_LAT):5.1f}m {cells:4.0f} │ "
                      f"{we:6.1f}s ({we / truth * 100:5.1f}%) +{w_add:5.1f} │ "
                      f"{ce:6.1f}s ({ce / truth * 100:5.1f}%) +{c_add:5.1f}")


def summarise_by_ratio(rows: list[dict]) -> None:
    """축을 바꿔 본다 — 오차를 정하는 것은 공원 크기도 셀 크기도 아니라 **둘의 비**다.

    이 표가 첫 표보다 중요하다. 공원별·반지름별로 읽으면 조합마다 다른 숫자를 외워야
    하지만, 비로 접으면 규칙 한 줄이 나온다.
    """
    buckets: dict[float, list[float]] = {}
    for row in rows:
        if row["truth"] <= 0:
            continue
        for radius in RADII:
            ratio = row["side"] / cell_size_m(radius, ORIGIN_LAT)
            key = min((b for b in (1, 2, 3, 4, 5, 6, 7, 8, 10, 15) if ratio <= b), default=15)
            err = abs(row[f"w{int(radius)}"] - row["truth"]) / row["truth"] * 100
            buckets.setdefault(key, []).append(err)

    print("\n" + "=" * 62)
    print("면 한 변 ÷ 셀 반지름(실제 미터)  →  면적가중 근사 오차 (중앙값)")
    print("=" * 62)
    for key in sorted(buckets):
        vals = buckets[key]
        print(f"  비 ≤ {key:2}배 │ 오차 {statistics.median(vals):5.1f}% │ 표본 {len(vals):4}")


def scene(side: float, jitter: float, seed: int) -> dict:
    """한 시행을 눈으로 볼 수 있는 형태로. 숫자표가 감추는 것을 보려고 만든다.

    오차 3.9% 와 33% 가 표에서는 그냥 다른 숫자지만, 그려 보면 전자는 셀이 면을 촘촘히
    덮은 그림이고 후자는 셀 하나가 면을 통째로 삼킨 그림이다. 왜 비가 규칙인지는 보면 안다.
    """
    rng = random.Random(seed)
    region = square_region(side, f"park-{int(side)}")
    track = walk_track(route_points(side))
    truth = truth_dwell(track, region)

    fixes = to_fixes(track, rng, jitter)
    ended = fixes[-1].at + timedelta(seconds=1)
    computed = compute_facts("scene", "spike", STARTED, ended, fixes)
    exact = dwell_by_region(region_encounters(computed.segments, [region])).get(region.id, 0.0)

    project = _projector(region.ring[0][0], region.ring[0][1])
    ring = [project(la, ln) for la, ln in region.ring]

    def covered(lat: float, lng: float) -> bool:
        return _point_in_ring(*project(lat, lng), ring)

    grids = {}
    for radius in RADII:
        visits = cell_visits(computed.segments, radius)
        cells = []
        for visit in visits:
            samples = [inverse_mercator(mx, my)
                       for mx, my in hex_sample_points(*visit.cell, radius, 3)]
            inside = sum(1 for lat, lng in samples if covered(lat, lng))
            cells.append({
                "id": cell_id(visit.cell, radius),
                "ring": [[round(la, 6), round(ln, 6)] for la, ln in hex_boundary_latlng(*visit.cell, radius)],
                "dwell_s": round(visit.dwell_s, 2),
                "overlap": round(inside / len(samples), 3),
                "centre_in": covered(*hex_center_latlng(*visit.cell, radius)),
            })
        grids[str(int(radius))] = {
            "cells": cells,
            "weighted": round(region_dwell_from_cells(visits, region, radius, True), 1),
            "centre": round(region_dwell_from_cells(visits, region, radius, False), 1),
            "ratio": round(side / cell_size_m(radius, ORIGIN_LAT), 2),
        }

    return {
        "side": side, "jitter": jitter, "seed": seed,
        "truth": round(truth, 1), "exact": round(exact, 1),
        "region": [[round(la, 6), round(ln, 6)] for la, ln in region.ring],
        "track": [[round(v, 6) for v in to_latlng(x, y)] for _, x, y in track],
        "fixes": [[round(f.lat, 6), round(f.lng, 6)] for f in fixes],
        "grids": grids,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")     # 윈도우 콘솔 기본 코드페이지 대응
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=30, help="조합당 시행 수")
    parser.add_argument("--csv", help="행 단위 결과를 쓸 경로")
    parser.add_argument("--scenes", help="눈으로 볼 장면 묶음을 쓸 JSON 경로")
    args = parser.parse_args(argv)

    rows = [
        one_trial(side, jitter, seed)
        for side in PARK_SIDES
        for jitter in JITTERS
        for seed in range(args.trials)
    ]
    summarise(rows)
    summarise_by_ratio(rows)

    if args.scenes:
        payload = {
            "radii": [int(r) for r in RADII],
            "scenes": [scene(side, 12.0, 0) for side in PARK_SIDES],
        }
        with open(args.scenes, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        print(f"\n장면 {len(payload['scenes'])}개 → {args.scenes}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n행 {len(rows)}개 → {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
