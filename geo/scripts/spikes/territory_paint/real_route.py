"""실제 도로망(OSM)에서 한 집의 산책들을 만든다. 합성 정사각형을 대체하는 입력.

    uv run python -m scripts.spikes.territory_paint.real_route --json walks.json --cache osm.json

갈래는 `docs/explorations/walk/territory-paint.md`.

## 왜 필요한가

`region_fidelity.py` 는 정사각형 면과 그 위를 도는 합성 궤적으로 오차를 쟀다. 정사각형은
경계 대비 면적이 가장 유리한 모양이라 그 측정은 낙관적으로 편향돼 있다. 실제 산책은 길을
따라가므로 꺾이고, 좁은 골목을 지나고, 되돌아온다.

## 고리를 찾으려다 알게 된 것

처음엔 **닫힌 고리**를 뽑으려 했다 — 집에서 나가 집으로 오니 궤적이 고리일 테고, 그러면 면을
따로 그리지 않아도 면이 생긴다고 봤다. 한 길로 나가서 그 간선을 전부 막고 다른 길로 돌아오는
방식이었다.

**하나도 못 찾았다.** 아파트 단지 안쪽 길이 대부분 막다른 가지라, 나간 길을 막으면 돌아올 길이
없다. 알고리즘 실패가 아니라 **지형이 원래 그렇다**는 사실이었고, 그 지형에서 사람은 나갔던
길로 되돌아온다. 왕복 선은 넓이가 0 이다.

그래서 이 스크립트는 고리를 강요하지 않는다. 면은 궤적이 만드는 게 아니라 붓이 만든다
(`app/features/territory/paint.py`).

## 지금 하는 것

한 집에서 나가는 산책 여러 번. 목적지 3곳이 '늘 가는 곳'이고 나머지는 가끔이다 — 매번 새
목적지면 자주 가는 영역이라는 것이 애초에 없어서 칠한 지도가 균일해진다. 돌아오는 길은 다른
길이 있으면 그쪽으로, 없으면 왔던 길로 (그게 현실이다).
"""

import argparse
import heapq
import json
import math
import os
import random
import statistics
import sys
import urllib.parse
import urllib.request

OVERPASS = "https://overpass-api.de/api/interpreter"

# 대치·도곡동 + 양재천. 아파트 단지 · 골목 · 하천 산책로가 한 상자에 들어오는 자리다 —
# 국내에서 개를 데리고 도는 지형의 전형이고, 셋의 성격이 서로 다르다.
BBOX = (37.4855, 127.0480, 37.4955, 127.0620)

WALKABLE = (
    "footway|path|pedestrian|living_street|residential|"
    "service|track|steps|cycleway|unclassified|tertiary"
)

EARTH_R = 6_371_000.0


def fetch(cache: str | None) -> dict:
    if cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as handle:
            return json.load(handle)
    south, west, north, east = BBOX
    query = (
        f'[out:json][timeout:60];(way["highway"~"^({WALKABLE})$"]'
        f"({south},{west},{north},{east}););out body geom;"
    )
    request = urllib.request.Request(
        OVERPASS,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": "daengs-geo-spike/0.1"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    if cache:
        with open(cache, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    return payload


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    """두 좌표 사이 거리. 등장방형 — 이 상자(1.5km) 안에서 오차는 무시할 수준이다."""
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot(
        math.radians(b[1] - a[1]) * EARTH_R * math.cos(lat),
        math.radians(b[0] - a[0]) * EARTH_R,
    )


def build_graph(payload: dict):
    """way 목록 → 무향 그래프. 노드는 좌표를 7자리로 반올림해 동일성을 잡는다.

    OSM 은 교차로에서 노드를 공유하지만 `out geom` 은 노드 id 를 안 준다. 좌표가
    같으면 같은 점이다 — 7자리는 약 1cm 라 다른 점이 우연히 합쳐지지 않는다.
    """
    graph: dict[tuple, list[tuple]] = {}
    kinds: dict[tuple, str] = {}

    def key(point) -> tuple[float, float]:
        return (round(point["lat"], 7), round(point["lon"], 7))

    for way in payload["elements"]:
        geometry = way.get("geometry") or []
        highway = way.get("tags", {}).get("highway", "")
        name = way.get("tags", {}).get("name")
        for index in range(len(geometry) - 1):
            a, b = key(geometry[index]), key(geometry[index + 1])
            if a == b:
                continue
            distance = metres(a, b)
            graph.setdefault(a, []).append((b, distance, way["id"], name))
            graph.setdefault(b, []).append((a, distance, way["id"], name))
            kinds.setdefault(a, highway)
            kinds.setdefault(b, highway)
    return graph, kinds


def largest_component(graph: dict) -> set:
    """가장 큰 연결 요소. 상자에 잘려 떠 있는 조각 위에서 고리를 찾으면 실패한다."""
    seen, best = set(), set()
    for start in graph:
        if start in seen:
            continue
        stack, group = [start], set()
        while stack:
            node = stack.pop()
            if node in group:
                continue
            group.add(node)
            stack += [n for n, *_ in graph[node] if n not in group]
        seen |= group
        if len(group) > len(best):
            best = group
    return best


def dijkstra(graph: dict, start, blocked: set | None = None):
    """최단 거리·이전 노드. `blocked` 는 쓰지 않을 간선 쌍(frozenset)이다."""
    blocked = blocked or set()
    dist = {start: 0.0}
    prev: dict = {}
    queue = [(0.0, start)]
    while queue:
        d, node = heapq.heappop(queue)
        if d > dist.get(node, math.inf):
            continue
        for nxt, step, *_ in graph[node]:
            if frozenset((node, nxt)) in blocked:
                continue
            nd = d + step
            if nd < dist.get(nxt, math.inf):
                dist[nxt], prev[nxt] = nd, node
                heapq.heappush(queue, (nd, nxt))
    return dist, prev


def trace(prev: dict, start, end) -> list:
    path, node = [end], end
    while node != start:
        node = prev[node]
        path.append(node)
    return path[::-1]


def edges_of(path: list) -> set:
    return {frozenset((path[i], path[i + 1])) for i in range(len(path) - 1)}


def walks_from_home(graph: dict, component: set, home, rng, count: int) -> list[list]:
    """한 집에서 나가는 산책 여러 번. 고리를 강요하지 않는다.

    **고리를 안 찾는 이유**: 산책의 시작과 끝은 주인이 정하지 지형이 정하지 않는다. 처음엔
    간선이 겹치지 않는 회로를 찾으려 했는데 하나도 안 나왔다 — 아파트 단지 안쪽 길이 대부분
    막다른 가지라서다. 그건 알고리즘 실패가 아니라 **지형이 원래 그렇다**는 사실이었고,
    그 지형에서 사람은 나갔던 길로 되돌아온다.

    그래서 산책은 닫힌 면이 아니라 **왕복 선**이다. 면은 궤적이 만드는 게 아니라 붓이 만든다.

    반복을 만든다: 목적지 3곳이 '늘 가는 곳'이고 나머지는 가끔이다. 매번 새 목적지면
    자주 가는 영역이라는 것이 애초에 없어서 칠한 지도가 균일해진다.
    """
    dist, prev = dijkstra(graph, home)
    far = sorted(
        (n for n, d in dist.items() if n in component and 250 <= d <= 1100),
        key=lambda n: dist[n],
    )
    if len(far) < 8:
        return []
    usual = [far[int(len(far) * f)] for f in (0.35, 0.6, 0.85)]

    walks = []
    for _ in range(count):
        target = rng.choice(usual) if rng.random() < 0.7 else rng.choice(far)
        outbound = trace(prev, home, target)
        # 돌아오는 길: 다른 길이 있으면 그쪽으로, 없으면 왔던 길로 (그게 현실이다)
        inbound = None
        if rng.random() < 0.5:
            back_dist, back_prev = dijkstra(graph, target, blocked=edges_of(outbound))
            if home in back_dist and back_dist[home] < dist[target] * 2.2:
                inbound = trace(back_prev, target, home)
        if inbound is None:
            inbound = outbound[::-1]
        walks.append(outbound + inbound[1:])
    return walks


def path_length_m(path: list) -> float:
    return sum(metres(path[i], path[i + 1]) for i in range(len(path) - 1))


def bounds(path: list):
    lats = [p[0] for p in path]
    lngs = [p[1] for p in path]
    return (min(lats), min(lngs), max(lats), max(lngs))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", help="Overpass 응답 캐시 경로")
    parser.add_argument("--json", help="만든 산책들을 쓸 경로")
    parser.add_argument("--walks", type=int, default=24, help="만들 산책 횟수")
    args = parser.parse_args(argv)

    payload = fetch(args.cache)
    graph, kinds = build_graph(payload)
    component = largest_component(graph)
    print(f"보행 그래프: 노드 {len(graph)} · 최대 연결요소 {len(component)}")

    # 집은 아파트 단지 안쪽 길(service/residential). 산책은 현관에서 시작한다.
    homes = sorted(n for n in component if kinds.get(n) in ("service", "residential"))
    rng = random.Random(7)
    best: list = []
    home = None
    for candidate in homes[:: max(1, len(homes) // 30)]:
        walks = walks_from_home(graph, component, candidate, random.Random(7), args.walks)
        if not walks:
            continue
        lengths = [path_length_m(w) for w in walks]
        if not (900 <= sum(lengths) / len(lengths) <= 3000):
            continue
        if len(walks) > len(best):
            best, home = walks, candidate
        if len(best) >= args.walks:
            break

    if not best:
        print("이 상자에서 조건에 맞는 집을 못 찾았다")
        return 1

    lengths = [path_length_m(w) for w in best]
    distinct = len({tuple(w) for w in best})
    print(f"집: {home[0]:.5f}, {home[1]:.5f}")
    print(f"산책 {len(best)}회 · 서로 다른 경로 {distinct}가지")
    print(f"길이 중앙 {statistics.median(lengths):.0f}m "
          f"(최소 {min(lengths):.0f} · 최대 {max(lengths):.0f})")
    print(f"총 보행거리 {sum(lengths) / 1000:.1f}km")
    south, west, north, east = bounds([p for w in best for p in w])
    print(f"돌아다닌 상자 {metres((south, west), (north, west)):.0f}m "
          f"× {metres((south, west), (south, east)):.0f}m")
    _ = rng

    if args.json:
        payload_out = {
            "bbox": BBOX,
            "home": list(home),
            "walks": [[[round(p[0], 6), round(p[1], 6)] for p in w] for w in best],
            "lengths_m": [round(v, 1) for v in lengths],
        }
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload_out, handle, ensure_ascii=False)
        print(f"\n산책 {len(best)}회 → {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
