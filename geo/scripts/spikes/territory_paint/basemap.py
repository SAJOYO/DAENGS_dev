"""스파이크 뷰어에 넣을 OSM 배경 타일을 받아 data URI 로 굽는다.

    uv run python -m scripts.spikes.territory_paint.basemap --walks walks.json --out basemap.json
    uv run python -m scripts.spikes.territory_paint.basemap --scenes layer-scenes.json --out basemap.json
    uv run python -m scripts.spikes.territory_paint.basemap --bbox 37.485 127.041 37.493 127.056 --out basemap.json

## 왜 미리 받나

칠한 지도를 실제 지형 위에서 봐야 판단이 된다 — 어디가 하천이고 어디가 단지 안 길인지
모르면 물감이 무엇을 덮었는지 알 수 없다. 그런데 게시되는 뷰어(Artifact)는 외부 호스트를
막으므로 타일 서버를 실시간으로 부를 수 없다. 그래서 필요한 타일만 미리 받아 페이지에 굽는다.

**소량만 받는다.** OSM 타일 서버는 자원봉사로 돌아가며 대량 내려받기를 금지한다
(https://operations.osmfoundation.org/policies/tiles/). 이 스파이크가 쓰는 범위는 한 동네
한 줌(z17 기준 30장 미만)이고, 캐시가 있으면 다시 받지 않는다. 범위를 넓히거나 줌을 올려
수백 장이 되면 이 스크립트를 쓰지 말고 자체 타일이나 상용 지도를 써야 한다.

표시할 때 저작자 표시(© OpenStreetMap contributors)는 필수다 — 뷰어가 이미 달고 있다.
"""

import argparse
import base64
import json
import math
import os
import sys
import time
import urllib.request

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "DAENGS_geo-spike/0.1 (research; https://github.com/rkbuhtig/DAENGS_geo)"
MAX_TILES = 60          # 이 이상이면 손으로 판단하라고 멈춘다


def deg2num(lat: float, lng: float, zoom: float) -> tuple[float, float]:
    n = 2.0**zoom
    x = (lng + 180.0) / 360.0 * n
    rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2.0 * n
    return x, y


def num2deg(x: float, y: float, zoom: float) -> tuple[float, float]:
    n = 2.0**zoom
    lng = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lng


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--walks", help="real_route/persona_year 가 만든 경로 JSON")
    parser.add_argument("--scenes", help="layer_scenes 가 만든 JSON — bbox 를 그대로 쓴다")
    parser.add_argument("--bbox", nargs=4, type=float,
                        metavar=("SOUTH", "WEST", "NORTH", "EAST"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--zoom", type=int, default=17)
    parser.add_argument("--margin", type=float, default=0.0012, help="bbox 여유(도)")
    args = parser.parse_args(argv)

    # 입력 셋을 받는 이유: 장면 생성기가 이미 bbox 를 정확히 계산해 두는데, 그걸 쓰려고
    # 경로 배열 흉내를 낸 임시 파일을 만들게 하면 재현 명령이 그만큼 거짓말이 된다.
    if args.scenes:
        with open(args.scenes, encoding="utf-8") as handle:
            south, west, north, east = json.load(handle)["bbox"]
    elif args.bbox:
        south, west, north, east = args.bbox
    elif args.walks:
        with open(args.walks, encoding="utf-8") as handle:
            source = json.load(handle)
        points = [p for walk in source["walks"] for p in walk]
        lats = [p[0] for p in points]
        lngs = [p[1] for p in points]
        south, north = min(lats), max(lats)
        west, east = min(lngs), max(lngs)
    else:
        parser.error("--walks · --scenes · --bbox 중 하나는 있어야 한다")
    south, north = south - args.margin, north + args.margin
    west, east = west - args.margin, east + args.margin

    x0, y1 = deg2num(south, west, args.zoom)
    x1, y0 = deg2num(north, east, args.zoom)
    tx0, tx1 = math.floor(x0), math.floor(x1)
    ty0, ty1 = math.floor(y0), math.floor(y1)
    count = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    print(f"z{args.zoom} · {tx1 - tx0 + 1}×{ty1 - ty0 + 1} = {count} 타일")
    if count > MAX_TILES:
        print(f"타일이 {MAX_TILES} 장을 넘는다. 줌을 낮추거나 범위를 줄여라.")
        return 1

    cache_dir = os.path.join(os.path.dirname(args.out), "_tiles")
    os.makedirs(cache_dir, exist_ok=True)
    tiles = []
    fetched = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            cached = os.path.join(cache_dir, f"{args.zoom}_{tx}_{ty}.png")
            if os.path.exists(cached):
                with open(cached, "rb") as handle:
                    blob = handle.read()
            else:
                url = TILE_URL.format(z=args.zoom, x=tx, y=ty)
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=30) as response:
                    blob = response.read()
                with open(cached, "wb") as handle:
                    handle.write(blob)
                fetched += 1
                time.sleep(0.12)          # 예의. 자원봉사 서버다
            north_lat, west_lng = num2deg(tx, ty, args.zoom)
            south_lat, east_lng = num2deg(tx + 1, ty + 1, args.zoom)
            tiles.append({
                "b": "data:image/png;base64," + base64.b64encode(blob).decode(),
                "n": round(north_lat, 7), "w": round(west_lng, 7),
                "s": round(south_lat, 7), "e": round(east_lng, 7),
            })

    payload = {"zoom": args.zoom, "tiles": tiles,
               "attribution": "© OpenStreetMap contributors"}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    size = os.path.getsize(args.out)
    print(f"새로 받은 타일 {fetched}장 (나머지는 캐시) · {args.out} {size / 1024:.0f}KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
