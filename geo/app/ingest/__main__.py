"""`python -m app.ingest {full|incremental|pet-axes}` 실행점.

`pet-axes` 는 이미 저장된 `facility.pet` 에서 축을 다시 파생할 뿐 원천을 호출하지 않는다 —
그래서 서비스 키를 요구하지 않는다.
"""

import argparse
import asyncio
import json

from app.core.config import settings
from app.core.db import SessionLocal
from app.ingest.mois import MoisClient
from app.ingest.mois_store import MoisStore
from app.ingest.mois_sync import SyncMode, sync_source
from app.ingest.pet_axes import derive_all
from app.place.source_catalog import MOIS_SOURCES as SOURCES

# 원천을 호출하지 않는 모드. 서비스 키 검사에서 빠진다.
LOCAL_MODES = ("pet-axes",)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="공공데이터를 PostGIS에 동기화")
    parser.add_argument("mode", choices=("full", "incremental", "pet-axes"))
    parser.add_argument("--kind", choices=("all", "hospital", "pharmacy"), default="all")
    parser.add_argument("--source", action="append",
                        help="pet-axes: 이 원천만 (여러 번 지정 가능). 미지정 = 전부")
    parser.add_argument("--all", action="store_true",
                        help="pet-axes: 미채움만이 아니라 전량 재파생 (문턱값 변경 후)")
    return parser


async def _run_pet_axes(redo: bool, sources: list[str] | None) -> None:
    async with SessionLocal() as session:
        stats = await derive_all(session, redo=redo, sources=tuple(sources) if sources else None)
    print(json.dumps(stats.to_dict(), ensure_ascii=False))


async def _run(mode: SyncMode, kind: str) -> None:
    selected = list(SOURCES.values()) if kind == "all" else [SOURCES[kind]]
    async with MoisClient(
        settings.data_go_kr_service_key,
        page_size=settings.mois_page_size,
    ) as client:
        for source in selected:
            async with SessionLocal() as session:
                stats = await sync_source(
                    client,
                    MoisStore(session),
                    source,
                    mode=mode,
                    overlap_days=settings.mois_sync_overlap_days,
                )
                print(json.dumps(stats.to_dict(), ensure_ascii=False))


def main() -> None:
    args = _parser().parse_args()
    if args.mode in LOCAL_MODES:
        asyncio.run(_run_pet_axes(args.all, args.source))
        return
    if not settings.data_go_kr_service_key:
        raise SystemExit("DAENGS_DATA_GO_KR_SERVICE_KEY is required")
    asyncio.run(_run(args.mode, args.kind))


if __name__ == "__main__":
    main()
