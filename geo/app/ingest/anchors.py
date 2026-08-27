"""보안등 원본 → 점령 앵커 선별. 좌표만 쓰고 결정론이다.

    python -m app.ingest.anchors lamps.ndjson            # 적재
    python -m app.ingest.anchors lamps.ndjson --dry-run  # 세보기만

**육각 격자인 이유**: 사각 격자는 이웃이 변(G)과 대각(1.41G) 두 거리라 앵커 간격이
방향마다 달라진다. 육각은 이웃 6개가 등거리다. 앱의 LocalHexCellIndexer 와 같은
투영·축좌표 수학을 써서 셀 id 체계를 공유한다 (`app/geo/cells.py`).

반지름은 **격자 단위**다 — Web Mercator 평면이라 위도 37.5° 에서 1 단위가 실제 0.79m 다.
115 단위는 실제 91m 이고 셀 간격은 158m 다. 선별은 같은 셀 안의 상대 비교라 이 배율에
영향받지 않지만, 간격을 미터로 말할 때는 `cells.cell_size_m` 을 거친다.

**중심 우선인 이유**: 셀당 1개만 뽑아도 두 앵커가 셀 경계에 붙으면 간격이 0에
가까워진다. 중심에 가까운 후보를 고르면 선택점이 셀 중심으로 몰려 이웃과 셀 간격만큼
벌어진다. 후보가 없는 셀은 비운다 — 없는 자리에 앵커를 만들지 않는다.
"""

import argparse
import asyncio
import json
import math
from collections import defaultdict
from datetime import date

from sqlalchemy import text

from app.core.db import SessionLocal
from app.geo.cells import ANCHOR_RADIUS_U as HEX_RADIUS_U
from app.geo.cells import hex_cell, hex_center, mercator

SOURCE = "lamp"

# 한전주 = 실제 전봇대. 서사와 현장 인지 모두 이쪽이 낫다.
KIND_RANK = {"한전주": 0, "전용주": 1, "통신주": 2, "건축물": 3}
UNKNOWN = "unknown"

_INSERT = text("""
INSERT INTO anchor (cell, source, kind, location, instt, as_of)
VALUES (:cell, :source, :kind,
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :instt, :as_of)
ON CONFLICT (source, cell) DO UPDATE
   SET kind = EXCLUDED.kind, location = EXCLUDED.location,
       instt = EXCLUDED.instt, as_of = EXCLUDED.as_of
""")


def _as_of(value: str | None) -> date | None:
    text_value = (value or "").strip()[:10]
    try:
        return date.fromisoformat(text_value)
    except ValueError:
        return None


def read_lamps(path: str):
    """NDJSON 한 줄 = 보안등 하나. 좌표 없거나 국내 밖이면 버린다."""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            try:
                lat, lng = float(row["latitude"]), float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (32.5 <= lat <= 39.0 and 124.0 <= lng <= 132.0):
                continue
            kind = (row.get("installationType") or "").strip() or UNKNOWN
            yield {
                "lat": lat,
                "lng": lng,
                "kind": kind,
                "instt": (row.get("insttNm") or "").strip() or None,
                "as_of": _as_of(row.get("referenceDate")),
            }


def select(points, radius_u: float = HEX_RADIUS_U) -> list[dict]:
    """셀당 1개. 우선순위: 설치형태 → 셀 중심까지 거리 → 좌표(완전 결정론)."""
    cells: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for point in points:
        cells[hex_cell(point["lat"], point["lng"], radius_u)].append(point)

    picked = []
    for (q, r), members in cells.items():
        cx, cy = hex_center(q, r, radius_u)
        best = min(
            members,
            key=lambda p: (
                KIND_RANK.get(p["kind"], 9),
                math.dist(mercator(p["lat"], p["lng"]), (cx, cy)),
                p["lat"],
                p["lng"],
            ),
        )
        picked.append(
            {**best, "cell": f"anchor-hex:{round(radius_u)}:{q}:{r}", "source": SOURCE}
        )
    return picked


async def _store(rows: list[dict]) -> None:
    async with SessionLocal() as session:
        for start in range(0, len(rows), 5_000):
            await session.execute(_INSERT, rows[start : start + 5_000])
            await session.commit()
            print(f"  적재 {min(start + 5_000, len(rows)):,}/{len(rows):,}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="보안등 NDJSON → 점령 앵커")
    parser.add_argument("path")
    parser.add_argument("--radius", type=float, default=HEX_RADIUS_U)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    points = list(read_lamps(args.path))
    anchors = select(points, args.radius)
    kinds = defaultdict(int)
    for anchor in anchors:
        kinds[anchor["kind"]] += 1
    print(f"입력 {len(points):,} → 앵커 {len(anchors):,} (육각 {args.radius:.0f}m)")
    for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {kind:8} {count:8,}  {100 * count / len(anchors):5.1f}%")

    if args.dry_run:
        return
    asyncio.run(_store(anchors))
    print("완료")


if __name__ == "__main__":
    main()
