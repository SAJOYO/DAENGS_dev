"""페르소나 1년치 가상 산책 — 정답지(ground truth)를 심어서 만든다.

    uv run python -m scripts.spikes.territory_paint.persona_year --cache osm.json --check
    uv run python -m scripts.spikes.territory_paint.persona_year --cache osm.json --json personas.json

## 왜 필요한가

셀로판 모델의 주장은 "산책을 독립된 장으로 보존하고 태그로 다시 골라 겹치면, 전체 누적에서
사라지는 행동 맥락을 복구할 수 있다" 이다. 그 주장을 **재려면 정답을 알아야 한다.**

실기기 데이터에는 정답이 없다 — 누가 왜 그 길로 갔는지 아무도 모른다. 그래서 패턴을
**심은** 산책을 만든다. 5배 규칙 측정에 참값 궤적이 필요했던 것과 같은 구조다.

## 관측값과 정답지를 가른다

    observable   walk_id · started_at · route     — 질의층이 보는 전부
    truth_only   family · intended                — 평가기만 본다

`truth_only` 는 `LayerSpec` 이 **절대 보면 안 된다.** 보면 실험이 자기 답을 베낀다.
파생 태그(계절·시간대)도 여기서 안 만든다 — `started_at` 에서 질의층이 뽑는다. 그래야
"태그를 파생할 수 있나" 까지 검증 대상에 들어온다.

## 페르소나 — 난이도 순

    A  null       패턴 없음. 계절·시간대별 횟수를 **균형 고정**              거짓 양성 통제
    B  seasonal   봄가을 공원 / 여름 하천 / 겨울 골목                        단일 축 회수
    C  time       아침 골목 / 저녁 공원                                      다른 단일 축
    D  drift      분기마다 갈 곳이 하나씩 는다 (이전 것도 계속)              활동권 확장
    E  correlated 여름은 대부분 밤 · 밤은 하천                               얽힌 태그 자르기
    F1 mirror-a   봄여름 하천 / 가을겨울 공원  ┐ 연간 누적은 같고            숨은 구조 회수
    F2 mirror-b   봄여름 공원 / 가을겨울 하천  ┘ 조건부는 정반대

A 를 **확률이 아니라 균형 고정**으로 만드는 이유: 확률로 뽑으면 표본 잡음만으로 계절 간
그림이 달라져서, 시스템이 멀쩡한데도 "거짓 양성 없음" 기준이 깨진다.

F1/F2 가 이 실험의 결정적 쌍이다. 연간 누적 지도가 서로 거의 같으므로 **누적만 저장했다면
구별 자체가 불가능**한데, 장을 남겼기 때문에 계절 조건에서 정반대로 갈린다.
"장으로 남기면 이런 게 된다" 가 아니라 "안 남기면 이걸 잃는다" 를 보인다.
"""

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from scripts.spikes.territory_paint.real_route import (
    build_graph,
    dijkstra,
    fetch,
    largest_component,
    path_length_m,
    trace,
)

YEAR = 2026
WALK_SPEED_MPS = 1.2

# 경로 family — 목적지가 어떤 성격의 길에 있나. 공간적으로 갈려야 정답 영역이 안 겹친다.
FAMILY_HIGHWAYS = {
    "river": ("cycleway",),                      # 양재천 자전거도로
    "park": ("path", "footway", "pedestrian"),   # 보행 전용 산책로
    "alley": ("service", "residential"),         # 단지 골목
    "bigroad": ("tertiary", "unclassified"),     # 큰길
}

# 시간대. 이름은 질의층이 started_at 에서 다시 뽑는다 — 여기 값은 시각을 만들 때만 쓴다.
TIME_BANDS = {
    "morning": (7, 9),
    "day": (12, 16),
    "evening": (18, 20),
    "night": (21, 23),
}
SEASON_MONTHS = {
    "spring": (3, 4, 5),
    "summer": (6, 7, 8),
    "autumn": (9, 10, 11),
    "winter": (12, 1, 2),
}


# 페르소나가 실제로 쓰는 family. 집을 고를 때 **이 조합만** 갈라지면 된다 —
# 안 쓰는 family 까지 분리를 요구하면 쓸 수 있는 집이 없어진다.
USED_FAMILIES = {
    "null-balanced": ("alley", "park", "river"),
    "seasonal": ("alley", "park", "river"),
    "time-of-day": ("alley", "park"),
    # D 는 분기마다 갈 곳이 하나씩 는다. 네 family 를 다 요구해 봤더니 이 동네에는 그런 집이
    # 하나도 없어서(전부 겹침 상한에 걸림) 셋으로 둔다 — 그러면 Q1→Q2→Q3 가 확장이고
    # Q4 는 Q3 와 같은 집합이라 **정체**다. 평가 기준이 그 모양을 그대로 말해야 한다.
    "drift": ("alley", "park", "river"),
    "correlated": ("alley", "river"),
    "mirror-a": ("park", "river"),
    "mirror-b": ("park", "river"),
}

# 경로 노드 Jaccard 상한. 넘으면 그 집을 버리고 다음 집을 본다.
# 셀 단위로 재려면 칠해야 하는데(paint 의존), 노드 겹침이 충분히 좋은 대리 지표다.
MAX_ROUTE_OVERLAP = 0.30


@dataclass(frozen=True)
class Walk:
    walk_id: str
    persona: str
    started_at: datetime
    route: list                 # [(lat, lng), ...] — 관측값
    family: str                 # truth only
    intended: str               # truth only


def node_family(kinds: dict, node) -> str | None:
    highway = kinds.get(node)
    for family, types in FAMILY_HIGHWAYS.items():
        if highway in types:
            return family
    return None


def pick_routes(graph, kinds, component, home, families: tuple[str, ...]) -> dict:
    """family 마다 경로 하나. **경로가 갈리도록** 고른다.

    처음엔 목적지끼리 멀도록 골랐는데 그것으로는 부족했다 — 목적지가 멀어도 가는 길이 같은
    복도를 공유하면 정답 영역이 겹친다(첫 시도 Jaccard 0.2~0.46). 그래서 후보마다 실제 경로를
    뽑아 **이미 고른 경로와 노드를 가장 적게 공유하는 것**을 집는다.

    집 앞 구간은 어차피 공유된다(왕복이니까). 그건 현실이고 평가기가 배타 영역만 세는 것으로
    다룬다 — 여기서는 그 공유를 최소로 줄이는 것까지만 한다.

    거리대 400~900m 는 개 산책 한 번의 편도로 현실적인 범위다.
    """
    dist, prev = dijkstra(graph, home)
    pool: dict[str, list] = {f: [] for f in families}
    for node, d in dist.items():
        if node not in component or not (400 <= d <= 900):
            continue
        family = node_family(kinds, node)
        if family in pool:
            pool[family].append(node)

    chosen: dict[str, list] = {}
    taken: set = set()
    for family in families:
        best, best_key = None, None
        for node in sorted(pool[family]):
            out = trace(prev, home, node)
            shared = len(taken & set(out))
            key = (shared, -dist[node], node)      # 적게 겹치고, 그중 먼 것
            if best_key is None or key < best_key:
                best, best_key = out, key
        if best is None:
            continue
        chosen[family] = best + best[::-1][1:]     # 왕복 — 나갔던 길로 돌아온다
        taken |= set(best)
    return chosen


def route_overlap(routes: dict, used: tuple[str, ...]) -> float:
    """이 페르소나가 쓰는 family 들끼리 경로가 얼마나 겹치나 (최대 Jaccard).

    왕복이라 집 앞 구간은 반드시 공유된다. 문제는 그 너머까지 같은 복도를 쓰는 경우인데,
    그러면 정답 영역이 서로 파묻혀 회수율이 뜻을 잃는다.
    """
    worst = 0.0
    for i, a in enumerate(used):
        for b in used[i + 1:]:
            sa, sb = set(routes[a]), set(routes[b])
            union = sa | sb
            if union:
                worst = max(worst, len(sa & sb) / len(union))
    return worst


def stamp(rng: random.Random, month: int, band: str) -> datetime:
    low, high = TIME_BANDS[band]
    day = rng.randint(1, 28)
    hour = rng.randint(low, high - 1)
    return datetime(YEAR, month, day, hour, rng.randint(0, 59), tzinfo=UTC)


def _emit(walks, plans, persona, routes, rng, month, band, family, intended, count):
    """같은 (달, 시간대, family) 조합을 `count` 번. 날짜·분만 흔들린다."""
    for _ in range(count):
        route = routes.get(family)
        if not route:
            continue
        walks.append(Walk(
            walk_id=f"{persona}-{len(walks):04d}",
            persona=persona,
            started_at=stamp(rng, month, band),
            route=route,
            family=family,
            intended=intended,
        ))
    _ = plans


def build_persona(persona: str, kind: str, graph, kinds, component, home, per_month: int):
    """한 사람의 1년. 페르소나마다 무엇을 심는지가 다르다."""
    rng = random.Random(sum(ord(c) for c in persona) * 7919)
    families = ("river", "park", "alley", "bigroad")
    routes = pick_routes(graph, kinds, component, home, families)
    used = USED_FAMILIES[kind]
    if any(f not in routes for f in used):
        return None, None
    overlap = route_overlap(routes, used)
    if overlap > MAX_ROUTE_OVERLAP:
        return None, None

    walks: list[Walk] = []
    have = list(routes)

    def has(*names):
        return [n for n in names if n in routes] or have[:1]

    for season, months in SEASON_MONTHS.items():
        for month in months:
            if kind == "null-balanced":
                # 계절·시간대·경로를 전부 균형 고정. 확률을 쓰지 않는다.
                three = has("alley", "park", "river")[:3] or have[:3]
                each = max(1, per_month // (len(three) * 2))
                for family in three:
                    for band in ("morning", "evening"):
                        _emit(walks, None, persona, routes, rng, month, band,
                              family, "none", each)

            elif kind == "seasonal":
                family = {"spring": "park", "summer": "river",
                          "autumn": "park", "winter": "alley"}[season]
                family = has(family)[0]
                for band in ("morning", "evening"):
                    _emit(walks, None, persona, routes, rng, month, band,
                          family, f"{season}_{family}", per_month // 2)

            elif kind == "time-of-day":
                for band, family in (("morning", "alley"), ("evening", "park")):
                    _emit(walks, None, persona, routes, rng, month, band,
                          has(family)[0], f"{band}_{family}", per_month // 2)

            elif kind == "drift":
                # 활동권이 **넓어진다** — 분기마다 갈 곳이 하나씩 늘고 이전 것도 계속 간다.
                #
                # 처음엔 "옮겨간다"로 짰다(1~4월 골목, 5~8월 공원, 9~12월 하천). 그건 확장이
                # 아니라 이주라, 두 family 가 섞인 분기가 순수 분기보다 넓어져 support 가
                # 205→338→410→216 로 오르내렸다. 평가기가 그 모순을 잡았다.
                order = [f for f in ("alley", "park", "river", "bigroad") if f in routes]
                active = order[: min(len(order), (month - 1) // 3 + 1)]
                for band in ("morning", "evening"):
                    share = max(1, (per_month // 2) // len(active))
                    for family in active:
                        _emit(walks, None, persona, routes, rng, month, band,
                              family, f"q{(month - 1) // 3 + 1}_{family}", share)

            elif kind == "correlated":
                # 여름은 대부분 밤, 밤은 하천. 낮은 어느 계절이든 골목.
                # 여름 밤 비중. 0.85 로 두면 여름∩낮 슬라이스가 연 12 회밖에 안 남아
                # "하천이 사라졌다" 를 n=12 로 주장하게 된다. 상관은 충분히 세게 두되
                # 가장 얇은 슬라이스가 두 자릿수 중반은 되도록 0.80 으로 낮춘다.
                night_share = 0.80 if season == "summer" else 0.4
                night = max(1, round(per_month * night_share))
                day = max(1, per_month - night)
                _emit(walks, None, persona, routes, rng, month, "night",
                      has("river")[0], f"{season}_night_river", night)
                _emit(walks, None, persona, routes, rng, month, "day",
                      has("alley")[0], f"{season}_day_alley", day)

            elif kind in ("mirror-a", "mirror-b"):
                warm = season in ("spring", "summer")
                first, second = ("river", "park") if kind == "mirror-a" else ("park", "river")
                family = has(first if warm else second)[0]
                for band in ("morning", "evening"):
                    _emit(walks, None, persona, routes, rng, month, band,
                          family, f"{season}_{family}", per_month // 2)

    walks.sort(key=lambda w: w.started_at)
    return walks, {"home": list(home), "kind": kind,
                   "route_families": sorted(routes),
                   "used_families": list(used),
                   "route_overlap": round(overlap, 3)}


PERSONAS = [
    ("A", "null-balanced"),
    ("B", "seasonal"),
    ("C", "time-of-day"),
    ("D", "drift"),
    ("E", "correlated"),
    ("F1", "mirror-a"),
    ("F2", "mirror-b"),
]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", help="Overpass 응답 캐시")
    parser.add_argument("--json", help="만든 산책을 쓸 경로")
    parser.add_argument("--per-month", type=int, default=30, help="사람당 월 산책 수")
    parser.add_argument("--check", action="store_true", help="정답 영역 분리도만 보고 끝")
    args = parser.parse_args(argv)

    payload = fetch(args.cache)
    graph, kinds = build_graph(payload)
    component = largest_component(graph)
    homes = sorted(n for n in component if kinds.get(n) in ("service", "residential"))
    print(f"보행 그래프 노드 {len(graph)} · 최대 연결요소 {len(component)} · 집 후보 {len(homes)}")

    made, meta = [], {}
    used_homes: set = set()
    mirror_home = None
    for persona, kind in PERSONAS:
        # F1·F2 는 **같은 집·같은 경로**를 써야 한다. 집이 다르면 연간 누적이 애초에 다르고,
        # 그러면 "누적은 같은데 조건부는 반대" 라는 이 쌍의 존재 이유가 사라진다.
        if kind == "mirror-b" and mirror_home is not None:
            walks, info = build_persona(persona, kind, graph, kinds, component,
                                        mirror_home, args.per_month)
            if walks:
                made.append((persona, walks))
                meta[persona] = info
            continue
        # 집 후보 전체를 본다. 커서를 앞으로만 밀면 뒤 페르소나가 굶는다 — 분리도 게이트를
        # 통과하는 집은 드문드문 있고, 앞 사람이 지나친 자리에 뒤 사람의 답이 있을 수 있다.
        for home in homes:
            if home in used_homes:
                continue
            walks, info = build_persona(persona, kind, graph, kinds, component,
                                        home, args.per_month)
            if walks:
                made.append((persona, walks))
                meta[persona] = info
                used_homes.add(home)
                if kind == "mirror-a":
                    mirror_home = home
                break
        else:
            print(f"  {persona}: 분리도 게이트를 통과하는 집이 없다")

    print(f"\n  {'':3} {'유형':<14} {'산책':>5} {'중앙 길이':>9} {'겹침':>6}  family 분포")
    for persona, walks in made:
        counts: dict[str, int] = {}
        for w in walks:
            counts[w.family] = counts.get(w.family, 0) + 1
        lengths = sorted(path_length_m(w.route) for w in walks)
        print(f"  {persona:3} {meta[persona]['kind']:<14} {len(walks):5} "
              f"{lengths[len(lengths) // 2]:7.0f}m {meta[persona]['route_overlap']:6.3f}  "
              f"{dict(sorted(counts.items()))}")

    if args.check:
        _report_slices(made)
        _report_separation(made, meta)
        return 0

    if args.json:
        out = {
            "year": YEAR,
            "personas": [
                {
                    "id": persona,
                    **meta[persona],
                    "walks": [
                        {
                            "walk_id": w.walk_id,
                            "started_at": w.started_at.isoformat(),
                            "route": [[round(p[0], 6), round(p[1], 6)] for p in w.route],
                            "truth_only": {"family": w.family, "intended": w.intended},
                        }
                        for w in walks
                    ],
                }
                for persona, walks in made
            ],
        }
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(out, handle, ensure_ascii=False)
        total = sum(len(w) for _, w in made)
        print(f"\n페르소나 {len(made)}명 · 산책 {total}회 → {args.json}")
    return 0


def _report_slices(made) -> None:
    """계절 × 시간대 슬라이스별 산책 수. **가장 얇은 칸이 결론의 강도를 정한다.**

    D 절(비율은 분모와 함께)이 여기 그대로 적용된다 — "여름 낮에 하천이 사라졌다" 가
    n=12 에서 나온 말이면 약하다. 실험을 돌리기 전에 분모를 본다.
    """
    print("\n=== 계절 × 시간대 슬라이스 (가장 얇은 칸이 결론의 강도) ===")
    bands = list(TIME_BANDS)
    print(f"  {'':3} " + " ".join(f"{s[:6]:>8}" for s in SEASON_MONTHS)
          + "   | 시간대별 합")
    for persona, walks in made:
        grid: dict[tuple, int] = {}
        for w in walks:
            season = next(s for s, ms in SEASON_MONTHS.items() if w.started_at.month in ms)
            band = next(b for b, (lo, hi) in TIME_BANDS.items() if lo <= w.started_at.hour < hi)
            grid[(season, band)] = grid.get((season, band), 0) + 1
        thin = min((v for v in grid.values()), default=0)
        per_band = {b: sum(v for (s, bb), v in grid.items() if bb == b) for b in bands}
        detail = " ".join(f"{b}={n}" for b, n in per_band.items() if n)
        print(f"  {persona:3} " + " ".join(
            f"{sum(v for (s, b), v in grid.items() if s == season):>8}" for season in SEASON_MONTHS)
            + f"   | {detail}   최소슬라이스 {thin}")


def _report_separation(made, meta) -> None:
    """정답 영역이 서로 얼마나 겹치나. 많이 겹치면 회수율이 뜻을 잃는다."""
    from app.features.territory.paint import NARROW_STEP, paint_sheet
    from scripts.spikes.territory_paint.paint import segments_for

    print("\n=== 정답 영역 분리도 (family 별 셀 집합의 Jaccard) ===")
    rng = random.Random(3)
    for persona, walks in made[:3]:
        by_family: dict[str, set] = {}
        seen = set()
        for w in walks:
            if w.family in seen:
                continue
            seen.add(w.family)
            segs = segments_for([tuple(p) for p in w.route], rng)
            if not segs:
                continue
            sheet = paint_sheet(w.walk_id, w.started_at, segs, 15.0, NARROW_STEP)
            by_family[w.family] = set(sheet.occupancy)
        names = sorted(by_family)
        print(f"\n  {persona} ({meta[persona]['kind']}) — family {len(names)}종")
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                sa, sb = by_family[a], by_family[b]
                j = len(sa & sb) / len(sa | sb) if sa | sb else 0.0
                flag = "  ← 겹침 큼" if j > 0.35 else ""
                print(f"    {a:8} ∩ {b:8}  Jaccard {j:.3f}  "
                      f"({len(sa):4}칸 / {len(sb):4}칸){flag}")


if __name__ == "__main__":
    sys.exit(main())
