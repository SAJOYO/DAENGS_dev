"""전체/증분 동기화 오케스트레이션."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from app.ingest.mois import KST, MoisClient, MoisRecord, MoisSource, normalize, source_id

SyncMode = Literal["full", "incremental"]


class Store(Protocol):
    async def upsert(self, record: MoisRecord) -> bool: ...
    async def get_watermark(self, source: str) -> str | None: ...
    async def set_watermark(self, source: str, watermark: str) -> None: ...
    async def deactivate_missing(self, source: str, seen_ids: list[str]) -> int: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


@dataclass
class SyncStats:
    source: str
    mode: SyncMode
    updated_since: str | None = None
    received: int = 0
    stored: int = 0
    rejected: int = 0
    deactivated: int = 0
    reconciled: bool = False
    watermark: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def overlap_watermark(watermark: str | None, days: int) -> str | None:
    if not watermark:
        return None
    try:
        value = datetime.strptime(watermark, "%Y%m%d%H%M%S").replace(tzinfo=KST)
        value -= timedelta(days=days)
    except ValueError:
        return None
    return value.strftime("%Y%m%d%H%M%S")


async def sync_source(
    client: MoisClient,
    store: Store,
    source: MoisSource,
    *,
    mode: SyncMode,
    overlap_days: int = 3,
) -> SyncStats:
    current = await store.get_watermark(source.source) if mode == "incremental" else None
    updated_since = overlap_watermark(current, overlap_days) if mode == "incremental" else None
    stats = SyncStats(source=source.source, mode=mode, updated_since=updated_since)

    try:
        items = await client.fetch_all(source, updated_since=updated_since)
        stats.received = len(items)
        if mode == "full" and not items:
            raise RuntimeError(f"refusing empty full snapshot for {source.source}")

        seen: list[str] = []
        watermarks: list[str] = []
        for item in items:
            sid = source_id(item)
            if sid:
                seen.append(sid)
            try:
                record = normalize(item, source)
            except ValueError:
                stats.rejected += 1
                continue
            if record.watermark:
                watermarks.append(record.watermark)
            if await store.upsert(record):
                stats.stored += 1
            else:
                stats.rejected += 1

        # fetch_all이 모든 페이지를 받은 뒤에만 도달한다. 좌표 없는 신규 행도 seen에는 포함되어
        # 있으므로 저장하지 않더라도 기존 동일 레코드를 잘못 비활성화하지 않는다.
        if mode == "full":
            stats.deactivated = await store.deactivate_missing(source.source, seen)
            stats.reconciled = True

        if watermarks:
            stats.watermark = max(watermarks)
            await store.set_watermark(source.source, stats.watermark)
        else:
            stats.watermark = current
        await store.commit()
        return stats
    except Exception:
        await store.rollback()
        raise
