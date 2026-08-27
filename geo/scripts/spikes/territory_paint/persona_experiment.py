"""실험: 조건으로 장을 골라 겹치면 심어둔 행동 패턴이 회수되는가.

    uv run python -m scripts.spikes.territory_paint.persona_year --cache osm.json --json personas.json
    uv run python -m scripts.spikes.territory_paint.persona_experiment --personas personas.json

## 무엇을 묻나

셀로판 모델의 주장은 하나다.

> 산책을 독립된 장으로 보존하고 태그로 다시 골라 겹치면, **전체 누적에서는 사라지는**
> 행동 맥락을 복구할 수 있다.

`persona_year` 가 정답을 아는 1년치를 만들었다. 여기서는 질의층(`app/features/territory/layers.py`)만
써서 지도를 만들고, **정답지와 대조해 숫자로** 회수 여부를 판정한다.

`truth_only` 는 이 파일의 평가 함수만 본다. `LayerSpec` 은 절대 보지 않는다 — 보면 실험이
자기 답을 베낀다.

## 정답 영역은 배타 영역이다

산책이 왕복이라 집 앞 구간은 모든 family 가 공유한다. 그건 현실이고, 그 구간은 애초에
아무것도 구별하지 못한다. 그래서 회수율은 **그 family 만 닿는 칸**에서 잰다.

    exclusive(river) = cells(river) − ∪ cells(다른 family)

## ε 는 고르지 않고 A 에서 얻는다

"거의 같다" 를 눈으로 판정하지 않는다. 패턴을 심지 않은 A 의 조건 간 거리 최댓값이
**잡음의 크기**이고, 그것이 ε 다. B·C·F 가 의미 있으려면 그보다 뚜렷하게 멀어야 한다.
"""

import argparse
import dataclasses
import json
import math
import os
import pickle
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from app.features.territory.layers import (
    Aggregation,
    LayerSpec,
    Projection,
    Selector,
    derive_tags,
    mass_in,
    normalized_distance,
    rate_diff,
    render,
)
from app.features.territory.paint import NARROW_STEP, Cellophane, paint_sheet
from app.geo.cells import GRID_VERSION, Cell
from scripts.spikes.territory_paint.paint import segments_for

RADIUS_U = 15.0
PROFILE = NARROW_STEP
JITTER_SEED = 20260826


@dataclass
class Person:
    persona: str
    kind: str
    sheets: list[Cellophane]
    truth: dict[str, str]                 # walk_id → family  (평가 전용)
    exclusive: dict[str, set[Cell]]       # family → 그 family 만 닿는 칸


def spec(metric: str = "walks", **tags) -> LayerSpec:
    return LayerSpec(
        selector=Selector.of(**tags),
        aggregation=Aggregation(metric=metric),
        projection=Projection(radius_u=RADIUS_U, brush=PROFILE.name,
                              profile_fp=PROFILE.fingerprint),
    )


def load(path: str, cache: str | None = None) -> list[Person]:
    """장을 칠한다. 2,520 회 × 150ms ≈ 6 분이라 결과를 캐시에 둔다.

    캐시는 **칠한 결과**만 담는다 — 정답지 대조는 매번 다시 한다. 격자·붓이 바뀌면 캐시가
    무효이므로 키에 넣는다.

    담는 것은 dataclass 가 아니라 순수 자료다. 클래스로 저장하면 pickle 이 정의 모듈을
    이름으로 찾는데, `-m scripts.X` 로 돌린 것과 다른 스크립트에서 읽을 때 그 이름이 달라져
    깨진다(`__main__.Person`). 캐시는 진입점에 묶이면 안 된다.
    """
    key = (f"{GRID_VERSION}|{RADIUS_U:.0f}|{PROFILE.name}|{PROFILE.fingerprint}|"
           f"{JITTER_SEED}|{os.path.getmtime(path):.0f}")
    if cache and os.path.exists(cache):
        with open(cache, "rb") as handle:
            blob = pickle.load(handle)
        if blob.get("key") == key:
            print(f"장 캐시 사용 ({cache})")
            return [
                Person(row["persona"], row["kind"],
                       [Cellophane(**sheet) for sheet in row["sheets"]],
                       row["truth"], {f: set(c) for f, c in row["exclusive"].items()})
                for row in blob["people"]
            ]

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    rng = random.Random(JITTER_SEED)
    people = []
    for entry in payload["personas"]:
        sheets, truth = [], {}
        by_family: dict[str, set[Cell]] = {}
        for walk in entry["walks"]:
            route = [tuple(p) for p in walk["route"]]
            segments = segments_for(route, rng)
            if not segments:
                continue
            at = datetime.fromisoformat(walk["started_at"])
            sheet = paint_sheet(walk["walk_id"], at, segments, RADIUS_U, PROFILE)
            sheets.append(sheet)
            family = walk["truth_only"]["family"]          # 평가 전용
            truth[walk["walk_id"]] = family
            by_family.setdefault(family, set()).update(sheet.occupancy)
        exclusive = {
            family: cells - set().union(*(c for f, c in by_family.items() if f != family))
            for family, cells in by_family.items()
        } if len(by_family) > 1 else {f: set(c) for f, c in by_family.items()}
        people.append(Person(entry["id"], entry["kind"], sheets, truth, exclusive))
        print(f"  칠함 {entry['id']:3} {len(sheets):4}장")
    if cache:
        plain = [
            {
                "persona": p.persona, "kind": p.kind, "truth": p.truth,
                "exclusive": {f: list(c) for f, c in p.exclusive.items()},
                "sheets": [dataclasses.asdict(s) for s in p.sheets],
            }
            for p in people
        ]
        with open(cache, "wb") as handle:
            pickle.dump({"key": key, "people": plain}, handle)
    return people


def positive(field_values: dict[Cell, float]) -> dict[Cell, float]:
    return {cell: value for cell, value in field_values.items() if value > 0}


# ---- 판정 ---------------------------------------------------------------------------


def null_epsilon(person: Person) -> tuple[float, list[str]]:
    """A 의 조건 간 최대 거리 = 잡음의 크기. 이것이 ε 다."""
    lines, worst = [], 0.0
    seasons = ["spring", "summer", "autumn", "winter"]
    layers = {s: render(person.sheets, spec(season=s)) for s in seasons}
    for i, a in enumerate(seasons):
        for b in seasons[i + 1:]:
            d = normalized_distance(layers[a], layers[b])
            worst = max(worst, d)
            lines.append(f"    {a:7} vs {b:7}  거리 {d:.4f}  "
                         f"(n={layers[a].selected}/{layers[b].selected})")
    bands = ["morning", "evening"]
    ba = {b: render(person.sheets, spec(time_band=b)) for b in bands}
    d = normalized_distance(ba["morning"], ba["evening"])
    worst = max(worst, d)
    lines.append(f"    {'morning':7} vs {'evening':7}  거리 {d:.4f}  "
                 f"(n={ba['morning'].selected}/{ba['evening'].selected})")
    return worst, lines


def axis_recovery(person: Person, tag: str, a: str, b: str, family: str):
    """조건 A 와 B 를 갈랐을 때 양의 질량이 `family` 의 배타 영역에 얼마나 드나."""
    la = render(person.sheets, spec(**{tag: a}))
    lb = render(person.sheets, spec(**{tag: b}))
    field_values = positive(rate_diff(la, lb))
    region = person.exclusive.get(family, set())
    return {
        "recall": mass_in(field_values, region),
        "distance": normalized_distance(la, lb),
        "n_a": la.selected, "n_b": lb.selected,
        "region_cells": len(region),
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--personas", required=True)
    parser.add_argument("--json", help="평가 결과를 쓸 경로")
    parser.add_argument("--cache-sheets", help="칠한 장을 담아 둘 경로")
    args = parser.parse_args(argv)

    people = {p.persona: p for p in load(args.personas, args.cache_sheets)}
    print(f"페르소나 {len(people)}명 · 장 {sum(len(p.sheets) for p in people.values())}개 "
          f"· 격자 {RADIUS_U:.0f}단위 · 붓 {PROFILE.name}")
    for p in people.values():
        sizes = {f: len(c) for f, c in sorted(p.exclusive.items())}
        print(f"  {p.persona:3} {p.kind:<14} 배타 영역 {sizes}")

    results: dict = {}
    verdicts: list[tuple[str, bool, str]] = []

    # --- 기준 3 먼저: ε 를 A 에서 얻는다 -------------------------------------------
    print("\n[기준 3] Null 보존 — A 의 조건 간 거리가 곧 잡음 ε")
    eps, lines = null_epsilon(people["A"])
    for line in lines:
        print(line)
    print(f"    → ε = {eps:.4f}")
    results["epsilon"] = eps
    verdicts.append(("3. Null 보존 (A)", eps < 0.25,
                     f"ε={eps:.4f} — 패턴 없는 사람의 조건 간 거리"))

    # --- 기준 1: 단일 축 회수 -------------------------------------------------------
    print("\n[기준 1] 단일 축 회수")
    b = axis_recovery(people["B"], "season", "summer", "winter", "river")
    c = axis_recovery(people["C"], "time_band", "morning", "evening", "alley")
    for name, r in (("B 여름−겨울 → river", b), ("C 아침−저녁 → alley", c)):
        print(f"    {name:22} 회수 {r['recall']:.3f}  거리 {r['distance']:.4f} "
              f"(ε의 {r['distance'] / eps:.1f}배)  n={r['n_a']}/{r['n_b']}  "
              f"배타 {r['region_cells']}칸")
    results["axis"] = {"B": b, "C": c}
    verdicts.append(("1. 단일 축 회수 (B·C)",
                     b["recall"] > 0.5 and c["recall"] > 0.5
                     and min(b["distance"], c["distance"]) > 2 * eps,
                     f"회수 B={b['recall']:.3f} C={c['recall']:.3f}"))

    # --- 기준 2: 얽힌 태그 자르기 ---------------------------------------------------
    print("\n[기준 2] correlated-tag slicing — E")
    e = people["E"]
    layers = {
        "summer": render(e.sheets, spec(season="summer")),
        "night": render(e.sheets, spec(time_band="night")),
        "summer∩night": render(e.sheets, spec(season="summer", time_band="night")),
        "summer∩day": render(e.sheets, spec(season="summer", time_band="day")),
    }
    river = e.exclusive.get("river", set())
    slice_rows = {}
    for name, layer in layers.items():
        share = mass_in({c: p.walks for c, p in layer.canvas.items()}, river)
        slice_rows[name] = {"n": layer.selected, "river_share": share}
        print(f"    {name:14} n={layer.selected:4}  river 배타 질량 {share:.3f}")
    marginal = normalized_distance(layers["summer"], layers["night"])
    print(f"    summer 과 night 의 거리 {marginal:.4f} (ε의 {marginal / eps:.1f}배) "
          f"— 얽혀 있으니 작아야 한다")
    results["correlated"] = {"slices": slice_rows, "marginal_distance": marginal}
    night_share = slice_rows["summer∩night"]["river_share"]
    day_share = slice_rows["summer∩day"]["river_share"]
    detail = (f"여름∩밤 {night_share:.3f} vs 여름∩낮 {day_share:.3f} "
              f"(n={slice_rows['summer∩day']['n']})")
    verdicts.append(("2. 얽힌 태그 자르기 (E)", night_share > 3 * max(day_share, 1e-9), detail))

    # --- 기준 4: 숨은 구조 (F1/F2) --------------------------------------------------
    print("\n[기준 4] 숨은 구조 회수 — F1/F2")
    f1, f2 = people["F1"], people["F2"]
    annual = {k: render(v.sheets, spec()) for k, v in (("F1", f1), ("F2", f2))}
    annual_distance = normalized_distance(annual["F1"], annual["F2"])
    print(f"    연간 누적 거리 {annual_distance:.4f} (ε의 {annual_distance / eps:.1f}배) "
          f"— 누적만 저장했다면 여기서 끝이다")
    warm, cold = ("spring", "summer"), ("autumn", "winter")
    pairs = {}
    for name, person in (("F1", f1), ("F2", f2)):
        w = render(person.sheets, spec(season=warm[1]))
        c2 = render(person.sheets, spec(season=cold[1]))
        pairs[name] = rate_diff(w, c2)
        river = person.exclusive.get("river", set())
        share = mass_in(positive(pairs[name]), river)
        print(f"    {name} 여름−겨울 양의 질량 중 river {share:.3f} "
              f"(n={w.selected}/{c2.selected})")
    cells = set(pairs["F1"]) | set(pairs["F2"])
    dot = sum(pairs["F1"].get(x, 0.0) * pairs["F2"].get(x, 0.0) for x in cells)
    n1 = math.sqrt(sum(v * v for v in pairs["F1"].values()))
    n2 = math.sqrt(sum(v * v for v in pairs["F2"].values()))
    cosine = dot / (n1 * n2) if n1 and n2 else 0.0
    print(f"    두 차이 field 의 코사인 {cosine:+.3f} — 정반대면 −1 에 가깝다")
    results["mirror"] = {"annual_distance": annual_distance, "cosine": cosine}
    verdicts.append(("4. 숨은 구조 회수 (F1/F2)",
                     annual_distance < 2 * eps and cosine < -0.5,
                     f"연간거리 {annual_distance:.4f} · 코사인 {cosine:+.3f}"))

    # --- 기준 5: D 분기별 확장 ------------------------------------------------------
    #
    # D 는 갈 곳이 분기마다 하나씩 늘지만 family 가 셋뿐이라 **Q3 에서 다 찬다.** 그래서
    # Q1→Q2→Q3 는 확장이고 Q4 는 정체다. "단조 증가" 로 재면 Q4 의 지터 잡음(±2%)에 걸려
    # 시스템이 멀쩡한데도 실패한다 — A 를 균형 고정으로 만든 것과 같은 이유다.
    print("\n[기준 5] D — 활동권 확장 후 정체 (갈 곳이 Q3 에서 다 찬다)")
    d = people["D"]
    quarters = []
    for q in ("Q1", "Q2", "Q3", "Q4"):
        layer = render(d.sheets, spec(quarter=q))
        quarters.append(len(layer.support))
        print(f"    {q}  support {len(layer.support):4}칸  n={layer.selected}")
    growth = [b / a - 1 for a, b in pairwise(quarters)]
    expands = all(g > 0.10 for g in growth[:2])          # 새 갈 곳이 붙는 두 걸음
    plateaus = abs(growth[2]) < 0.10                     # 마지막은 같은 집합 — 잡음만
    print(f"    분기 간 증가율 {[f'{g:+.1%}' for g in growth]} "
          f"— 앞 둘은 확장(>+10%), 마지막은 정체(|·|<10%)")
    results["drift"] = {"support": quarters, "growth": growth}
    verdicts.append(("5. 활동권 확장 (D)", expands and plateaus,
                     f"support {quarters} · 증가율 {[f'{g:+.0%}' for g in growth]}"))

    # --- 기준 6: 재현성 -------------------------------------------------------------
    once = render(people["B"].sheets, spec(season="summer"))
    twice = render(people["B"].sheets, spec(season="summer"))
    same = (once.spec.fingerprint() == twice.spec.fingerprint()
            and once.canvas.keys() == twice.canvas.keys()
            and all(once.canvas[x].walks == twice.canvas[x].walks for x in once.canvas))
    verdicts.append(("6. 재현성", same, f"지문 {once.spec.fingerprint()}"))

    print("\n" + "=" * 72)
    for name, ok, detail in verdicts:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:26} {detail}")
    print("=" * 72)

    if args.json:
        results["verdicts"] = [{"name": n, "pass": ok, "detail": d} for n, ok, d in verdicts]
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=False, indent=2, default=str)
        print(f"\n평가 결과 → {args.json}")
    _ = derive_tags
    return 0 if all(ok for _, ok, _ in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
