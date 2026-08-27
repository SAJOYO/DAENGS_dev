"""CLI.

  python -m realtime config              # 키·경로가 실제로 읽히는지
  python -m realtime geo 37.4979 127.0276   # 위경도 → 격자·대표점
  python -m realtime walk 37.4979 127.0276  # 조립 → 판정 (검문소 D)

crawler·rag 와 같은 방식이다 — FastAPI 없이 단독으로 돈다 (RAG-001 원칙 1).
"""
from __future__ import annotations

import argparse
import sys

from . import config, geo

# 윈도우 콘솔 기본 인코딩(cp949)으로는 한글이 깨지고 일부 기호는 예외를 낸다 (crawler·rag CLI 와 같은 처리).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def cmd_config(args: argparse.Namespace) -> int:
    """키가 읽혔는지만 보여준다. **값은 절대 찍지 않는다** — 터미널이 곧 로그다 (RAG-012)."""
    print("키")
    for name, value in config.KEYS.items():
        raw = getattr(config.settings, name.lower()).strip()
        if not value:
            print(f"  [ ] {name:16s} 없음 — 발급처는 docs/data-sources.md §9")
            continue
        # 정규화가 실제로 걸렸는지가 이 명령의 존재 이유다. Encoding 키를 넣어 두고
        # "왜 403 이지"로 한나절을 쓰는 것이 §6.1 함정 1 이 실제로 일으킨 일이다.
        note = f"Encoding 키 → 디코딩함 ({len(raw)}자 → {len(value)}자)" if raw != value else "그대로"
        print(f"  [x] {name:16s} {len(value)}자, {note}")

    print("\n경로")
    print(f"  DATA_DIR       {config.DATA_DIR or '못 찾음 — DAENGS_DATA_DIR 로 알려줄 것'}")
    print(f"  REFERENCE_DIR  {config.REFERENCE_DIR or '-'}"
          f"{'' if config.REFERENCE_DIR is None or config.REFERENCE_DIR.exists() else '  (아직 없음)'}")

    print("\n예산")
    print(f"  요청 하나 {config.REQUEST_BUDGET_SEC}s · 개별 호출 {config.REQUEST_TIMEOUT_SEC}s  (RT-001 ⑤-b)")

    missing = [n for n, v in config.KEYS.items() if not v]
    if missing:
        # 없다고 실패로 만들지 않는다 — 키 하나가 비어도 나머지는 돌고,
        # 그 위에서 ⑤ 저하 정책이 UNKNOWN 을 만든다. CLI 가 그 설계를 앞질러 판정하면 안 된다.
        print(f"\n  {len(missing)}개 미설정: {', '.join(missing)}")
    return 0


def cmd_geo(args: argparse.Namespace) -> int:
    """위경도 하나를 조회 키로 바꿔 보여준다. 격자가 한 칸 어긋나도 API 는 정상 응답을 주므로
    눈으로 확인할 자리가 필요하다 — 검산 4지점은 `tests/test_geo.py` 에 박혀 있다."""
    here = geo.LatLon(args.lat, args.lon)
    grid = geo.to_grid(here)
    center = geo.to_latlon(grid)
    print(f"  입력    {here.lat:.6f}, {here.lon:.6f}")
    print(f"  격자    nx={grid.nx} ny={grid.ny}   (기상청 단기예보 5km 격자)")
    print(f"  대표점  {center.lat:.6f}, {center.lon:.6f}   "
          f"입력에서 {geo.haversine_km(here, center):.2f}km")
    return 0


def cmd_walk(args: argparse.Namespace) -> int:
    """조립 → 판정을 한 번에 (RT-002 ②-a). **FastAPI 없이 검문소 D 를 돌릴 자리다.**

    `--no-cache` 는 Redis 를 건드리지 않고 프로세스 메모리로만 돈다 — ④-c 가 "없어도 돈다"를
    말로만 두지 않으려면 눈으로 볼 수 있어야 한다.
    """
    from datetime import datetime

    from .cache import Cache, MemoryStore
    from .collect import collect
    from .rules import judge

    cache = Cache(MemoryStore()) if args.no_cache else Cache()
    now = datetime.now(config.KST)
    obs = collect(geo.LatLon(args.lat, args.lon), now, cache=cache)
    loc = obs.location

    print(f"  위치    {loc.label}  (격자 {loc.grid.nx},{loc.grid.ny})")
    print(f"  측정소  {loc.station or '-'}"
          f"{f' {loc.station_km}km' if loc.station_km is not None else ''}"
          f"   AWS {loc.aws_station or '없음 — ⑤-d 1순위 미발동'}   권역 {loc.region or '-'}")
    print(f"  관측 {len(obs.measurements)}건 · 상태 {len(obs.states)}건\n")

    print("  출처")
    for r in obs.providers:
        mark = "stale" if r.stale else ("ok" if r.ok else "FAIL")
        print(f"    {mark:5s} {r.provider.value:26s} {(r.reason or '')[:60]}")

    verdict = judge(obs, now)
    if verdict.grade is None:
        # ⑤-a — 기상청 격자가 통째로 없으면 판정 자체를 안 낸다. 모르는 것을 GOOD 이라고
        # 하지 않는 것과 같은 규율이고, 여기서 그것이 눈에 보여야 한다
        print("\n  판정    불가 — 필수 출처(기상청 격자)가 없다 (⑤-a)")
        return 1
    print(f"\n  판정    {verdict.grade.name}"
          f"{'  (' + ', '.join(a.name for a in verdict.dominant) + ')' if verdict.dominant else ''}")
    if verdict.unknown_axes:
        print(f"  모르는 축  {', '.join(a.name for a in verdict.unknown_axes)}"
              f"{'   → GOOD 으로 올리지 않음 (⑤-a)' if verdict.capped else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m realtime", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("config", help="키·경로가 실제로 읽히는지 확인")
    p_config.set_defaults(func=cmd_config)

    p_geo = sub.add_parser("geo", help="위경도 → 격자·대표점")
    p_geo.add_argument("lat", type=float)
    p_geo.add_argument("lon", type=float)
    p_geo.set_defaults(func=cmd_geo)

    p_walk = sub.add_parser("walk", help="조립 → 판정 (검문소 D)")
    p_walk.add_argument("lat", type=float)
    p_walk.add_argument("lon", type=float)
    p_walk.add_argument("--no-cache", action="store_true",
                        help="Redis 를 안 쓰고 프로세스 메모리로만 (④-c)")
    p_walk.set_defaults(func=cmd_walk)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
