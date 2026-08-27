"""뷰어용 장면 — 페르소나 × 고정 시나리오를 미리 겹쳐 둔다.

    uv run python -m scripts.spikes.territory_paint.layer_scenes --personas personas.json \\
        --cache-sheets sheets.pkl --out scenes.json

## 왜 고정 시나리오인가

미리 겹쳐 둔 조합만 실을 거면서 UI 를 임의 태그 조합처럼 만들면 **실제보다 되는 것처럼
보인다.** 그건 가짜 공급자가 설계의 모든 질문에 답해서 데이터의 한계가 안 보였던
결정 #51 의 UI 판이다. 그래서 목록을 여기서 못 박고 뷰어는 그 목록만 보여준다.

임의 조합 질의는 DB·API 로 갈 때 한다. 지금 묻는 것은 그게 아니라:

> 복구한 정보를 **사람이 지도에서 읽을 수 있는가.**

## 무엇을 내보내나

칸 좌표는 페르소나마다 한 번만 싣고 층은 그 색인을 가리킨다 — 13 장면이 같은 칸을 다시
적으면 파일이 그만큼 커진다.

층마다 `walks`(빈도) · `peak`(최대 세기) · `occ`(물감량)를 다 싣는 이유는 뷰어에서
**존재 연산과 값 연산을 갈라 보여주기** 위해서다. 비율은 `walks / selected` 라 분모도
같이 실어야 한다 — 뷰어가 두 층을 빼려면 그 분모가 필요하다.
"""

import argparse
import json
import sys

from app.features.territory.layers import Aggregation, LayerSpec, Projection, Selector, render
from app.geo.cells import GRID_VERSION, hex_center_latlng
from scripts.spikes.territory_paint.persona_experiment import PROFILE, RADIUS_U, load

# 뷰어가 보여줄 전부. 실험이 실제로 답한 조합만 남긴다.
SCENARIOS: tuple[tuple[str, str, dict], ...] = (
    ("all", "전체", {}),
    ("spring", "봄", {"season": "spring"}),
    ("summer", "여름", {"season": "summer"}),
    ("autumn", "가을", {"season": "autumn"}),
    ("winter", "겨울", {"season": "winter"}),
    ("morning", "아침", {"time_band": "morning"}),
    ("evening", "저녁", {"time_band": "evening"}),
    ("day", "낮", {"time_band": "day"}),
    ("night", "밤", {"time_band": "night"}),
    ("summer_night", "여름∩밤", {"season": "summer", "time_band": "night"}),
    ("summer_day", "여름∩낮", {"season": "summer", "time_band": "day"}),
    ("q1", "1분기", {"quarter": "Q1"}),
    ("q4", "4분기", {"quarter": "Q4"}),
)

# 문턱 두 단계만 낸다 — 슬라이더가 아니라 토글이다. 미리 겹쳐 둔 값만 있는데 연속 슬라이더로
# 보이면 임의 질의처럼 읽힌다(고정 시나리오와 같은 이유).
#
#   0.0   스친 것까지 전부
#   0.9   심 밴드에 든 통과만  — "옆을 지났을 뿐인 칸" 이 빠진다
MIN_PEAKS = (0.0, 0.9)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--personas", required=True)
    parser.add_argument("--cache-sheets")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    with open(args.personas, encoding="utf-8") as handle:
        source = json.load(handle)
    homes = {entry["id"]: entry["home"] for entry in source["personas"]}
    kinds = {entry["id"]: entry["kind"] for entry in source["personas"]}
    routes = {
        entry["id"]: [list(r) for r in {tuple(map(tuple, w["route"])) for w in entry["walks"]}]
        for entry in source["personas"]
    }

    people = load(args.personas, args.cache_sheets)
    out_personas = []
    south = west = 1e9
    north = east = -1e9

    print(f"\n  {'':4} {'유형':<14} " + " ".join(f"{lab:>7}" for _, lab, _ in SCENARIOS[:6]))
    for person in people:
        index: dict[tuple, int] = {}
        centres: list[list[float]] = []
        layers: dict[str, dict] = {}
        for key, _label, tags in SCENARIOS:
            for min_peak in MIN_PEAKS:
                spec = LayerSpec(
                    selector=Selector.of(**tags),
                    aggregation=Aggregation(metric="walks", min_peak=min_peak),
                    projection=Projection(radius_u=RADIUS_U, brush=PROFILE.name,
                                          profile_fp=PROFILE.fingerprint,
                                          grid_version=GRID_VERSION),
                )
                layer = render(person.sheets, spec)
                top = max((p.occupancy for p in layer.canvas.values()), default=1.0) or 1.0
                values = []
                for cell, paint in layer.canvas.items():
                    slot = index.get(cell)
                    if slot is None:
                        slot = index[cell] = len(centres)
                        lat, lng = hex_center_latlng(*cell, RADIUS_U)
                        centres.append([round(lat, 6), round(lng, 6)])
                    values.append([slot, paint.walks, round(paint.peak, 3),
                                   round(paint.occupancy / top, 4)])
                layers[f"{key}@{min_peak}"] = {
                    "selected": layer.selected,
                    "total": layer.total,
                    "fingerprint": layer.spec.fingerprint(),
                    "v": values,
                }

        for lat, lng in centres:
            south, north = min(south, lat), max(north, lat)
            west, east = min(west, lng), max(east, lng)

        out_personas.append({
            "id": person.persona,
            "kind": kinds[person.persona],
            "home": homes[person.persona],
            "cells": centres,
            "routes": routes[person.persona],
            "layers": layers,
        })
        counts = " ".join(f"{layers[k + '@0.0']['selected']:>7}" for k, _, _ in SCENARIOS[:6])
        print(f"  {person.persona:4} {kinds[person.persona]:<14} {counts}")

    payload = {
        "grid": {
            "radius_u": RADIUS_U,
            "brush": PROFILE.name,
            "grid_version": GRID_VERSION,
            "profile_fp": PROFILE.fingerprint,
            "bands": list(PROFILE.bands),
            "weights": list(PROFILE.weights),
        },
        "bbox": [round(south, 6), round(west, 6), round(north, 6), round(east, 6)],
        "scenarios": [{"key": k, "label": lab, "tags": t} for k, lab, t in SCENARIOS],
        "min_peaks": list(MIN_PEAKS),
        "personas": out_personas,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    cells = sum(len(p["cells"]) for p in out_personas)
    print(f"\n페르소나 {len(out_personas)}명 · 장면 {len(SCENARIOS)}종 · 칸 {cells}개 "
          f"→ {args.out}")
    print(f"bbox {payload['bbox']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
