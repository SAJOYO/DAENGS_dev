"""보안등 NDJSON을 140u 중립 점령지 게임판으로 선별해 원자적으로 교체한다.

사용법::

    python -m daengs_place.ingest.territory_sites lamps.ndjson
    python -m daengs_place.ingest.territory_sites lamps.ndjson --dry-run

각 육각 셀에서 실제 시설 좌표 하나만 고른다. 대표 시설과 논리 점령지를 분리하지 않는 것은
독립적인 교체 이력·수명 요구가 아직 없기 때문이다. 시설 종류는 적재 검수용으로만 보존하고
앱 API에는 내보내지 않는다.
"""

import argparse
import asyncio
import json
import math
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import date

from sqlalchemy import text

from daengs_place.core.db import SessionLocal
from daengs_place.territory.grid import (
    TERRITORY_SITE_RADIUS_U,
    hex_cell,
    hex_center,
    mercator,
    site_id,
)

SOURCE = "lamp"
MIN_PRODUCTION_SITES = 300_000
KIND_RANK = {"한전주": 0, "전용주": 1, "통신주": 2, "건축물": 3}
UNKNOWN_KIND = "unknown"

_INSERT = text("""
INSERT INTO territory_site (site_id, source, kind, location, instt, as_of)
VALUES (:site_id, :source, :kind,
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :instt, :as_of)
""")


def _as_of(value: str | None) -> date | None:
    text_value = (value or "").strip()[:10]
    try:
        return date.fromisoformat(text_value)
    except ValueError:
        return None


def read_lamps(path: str) -> Iterator[dict]:
    """NDJSON에서 국내 유효 좌표만 읽는다."""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            try:
                lat, lng = float(row["latitude"]), float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (32.5 <= lat <= 39.0 and 124.0 <= lng <= 132.0):
                continue
            yield {
                "lat": lat,
                "lng": lng,
                "kind": (row.get("installationType") or "").strip() or UNKNOWN_KIND,
                "instt": (row.get("insttNm") or "").strip() or None,
                "as_of": _as_of(row.get("referenceDate")),
            }


def _select(
    points: Iterable[dict],
    radius_u: float = TERRITORY_SITE_RADIUS_U,
) -> tuple[list[dict], int]:
    """후보 전체를 쌓지 않고 셀별 현재 승자만 보관한다."""
    cells: dict[tuple[int, int], tuple[tuple, dict]] = {}
    input_count = 0
    for point in points:
        input_count += 1
        cell = hex_cell(point["lat"], point["lng"], radius_u)
        cx, cy = hex_center(*cell, radius_u)
        rank = (
            KIND_RANK.get(point["kind"], 9),
            math.dist(mercator(point["lat"], point["lng"]), (cx, cy)),
            point["lat"],
            point["lng"],
        )
        current = cells.get(cell)
        if current is None or rank < current[0]:
            cells[cell] = (rank, point)

    picked = [
        {**winner, "site_id": site_id(cell, radius_u), "source": SOURCE}
        for cell, (_, winner) in cells.items()
    ]
    return picked, input_count


def select(points: Iterable[dict], radius_u: float = TERRITORY_SITE_RADIUS_U) -> list[dict]:
    """셀당 하나를 설치형태, 중심 거리, 좌표 순으로 결정론적으로 고른다."""
    return _select(points, radius_u)[0]


async def replace(rows: list[dict]) -> None:
    """lamp 세대를 한 트랜잭션에서 교체한다. 실패하면 기존 게임판이 그대로 남는다."""
    if not rows:
        raise ValueError("빈 점령지 스냅샷으로 기존 게임판을 지울 수 없습니다")
    async with SessionLocal() as session, session.begin():
        await session.execute(
            text("DELETE FROM territory_site WHERE source = :source"),
            {"source": SOURCE},
        )
        for start in range(0, len(rows), 5_000):
            await session.execute(_INSERT, rows[start : start + 5_000])
            print(f"  준비 {min(start + 5_000, len(rows)):,}/{len(rows):,}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="보안등 NDJSON → 140u 중립 점령지")
    parser.add_argument("path")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--minimum-sites",
        type=int,
        default=MIN_PRODUCTION_SITES,
        help="이 수보다 작은 스냅샷은 교체 거부 (기본: 300000)",
    )
    args = parser.parse_args()

    sites, input_count = _select(read_lamps(args.path))
    if not sites:
        raise SystemExit("유효한 국내 보안등 좌표가 없어 기존 게임판을 교체하지 않습니다")
    kinds = Counter(site["kind"] for site in sites)
    print(
        f"입력 {input_count:,} → 점령지 {len(sites):,} "
        f"(육각 {TERRITORY_SITE_RADIUS_U:.0f}u)"
    )
    for kind, count in kinds.most_common():
        print(f"  {kind:8} {count:8,}  {100 * count / len(sites):5.1f}%")

    if args.dry_run:
        return
    if len(sites) < args.minimum_sites:
        raise SystemExit(
            f"점령지 {len(sites):,}개는 교체 하한 {args.minimum_sites:,}개보다 작습니다"
        )
    asyncio.run(replace(sites))
    print("완료")


if __name__ == "__main__":
    main()
