"""한국관광공사 반려동물 동반여행 (KorPetTourService2) → facility 기반층 두 번째 원천.

    python -m daengs_place.ingest.kto                      # 증분 (기본)
    python -m daengs_place.ingest.kto --mode full          # 전량 재적재 + 사라진 행 정리
    python -m daengs_place.ingest.kto --details 300        # 반려동물 정책 상세를 300건까지 보강

**KCISA와 다른 점**: 이 원천은 안정 ID(`contentid`)와 항목별 `modifiedtime`을 준다.
그래서 스냅샷 통째 교체가 아니라 UPSERT + 워터마크 증분이 맞다.

**증분이 클라이언트 측인 이유**: `petTourSyncList2`의 `modifiedtime` 파라미터는
어떤 값을 넣어도 totalCount=0을 돌려준다 (2026-08-24 실측: 20250101/20260301/
20260601 전부 0, 파라미터 없으면 10,152). 서버 필터를 못 믿으므로 목록은 전량
받고 **적용을 증분으로** 한다 — 워터마크보다 새 항목만 UPSERT.

`showflag=0`(숨김·삭제)은 적재하지 않는다. 목록 10,152건 중 약 12%.

MOIS와 같은 원칙: 사용자 검색 시 호출 없음, 배치 전용. 좌표는 원천이 WGS84.
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import unquote

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.core.config import settings
from daengs_place.core.db import SessionLocal
from daengs_place.ingest.facility_store import (
    invalidate_pet_details_without_source_payload,
    prune_unseen,
    update_pet_detail,
    upsert_rows,
)
from daengs_place.ingest.linking import rebuild_links
from daengs_place.ingest.source_record_store import (
    pending_detail_refs,
    prune_source_records,
    record_detail_result,
    upsert_source_records,
)
from daengs_place.place.source_catalog import (
    KTO_KINDS as KINDS,
)
from daengs_place.place.source_facts.states import DetailAcquisitionState

SOURCE = "public:kto:pet_tour"
BASE_URL = "https://apis.data.go.kr/B551011/KorPetTourService2"


class KtoApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class DetailFetchResult:
    state: DetailAcquisitionState
    detail: dict | None = None


def _params(**extra: str) -> dict:
    return {
        "serviceKey": unquote(settings.kto_service_key.strip()),
        "MobileOS": "ETC",
        "MobileApp": "daengs",
        "_type": "json",
        **extra,
    }


def _body(payload: dict) -> dict:
    try:
        response = payload["response"]
        if str(response["header"]["resultCode"]) not in ("0", "00", "0000"):
            raise KtoApiError(response["header"].get("resultMsg", "unknown"))
        return response["body"]
    except (KeyError, TypeError) as exc:
        # 인증·트래픽 오류는 response 봉투가 아니라 OpenAPI_ServiceResponse로 온다
        raise KtoApiError(f"invalid KTO envelope: {str(payload)[:200]}") from exc


async def fetch_sync_list(client: httpx.AsyncClient) -> list[dict]:
    """`petTourSyncList2` 전량. showflag 를 포함하므로 숨김 항목까지 보인다."""
    items: list[dict] = []
    page = 1
    while True:
        r = await client.get(
            f"{BASE_URL}/petTourSyncList2",
            params=_params(numOfRows=str(settings.kto_page_size), pageNo=str(page)),
        )
        r.raise_for_status()
        body = _body(r.json())
        chunk = (body.get("items") or {}).get("item") or []
        if isinstance(chunk, dict):
            chunk = [chunk]
        items.extend(chunk)
        total = int(body["totalCount"])
        if page * settings.kto_page_size >= total:
            if len(items) < total:
                raise KtoApiError(f"received {len(items)} before totalCount={total}")
            return items
        page += 1


async def fetch_pet_detail(client: httpx.AsyncClient, content_id: str) -> DetailFetchResult:
    """상세 payload와 획득 실패/no-data를 구분한다.

    상세는 보강이지 본체가 아니다 — 상세 실패로 적재 전체가 죽으면 안 된다.
    """
    for attempt in range(4):
        r = await client.get(f"{BASE_URL}/detailPetTour2", params=_params(contentId=content_id))
        if r.status_code == 429:
            await asyncio.sleep(1.0 * (2**attempt))
            continue
        r.raise_for_status()
        break
    else:
        return DetailFetchResult(DetailAcquisitionState.FETCH_FAILED)
    try:
        payload = r.json()
    except ValueError as exc:
        raise KtoApiError("invalid KTO detail JSON") from exc
    body = _body(payload)
    if not isinstance(body, dict):
        raise KtoApiError("invalid KTO detail body")
    items = body.get("items") or {}
    if not isinstance(items, dict):
        raise KtoApiError("invalid KTO detail items")
    item = items.get("item") or []
    if isinstance(item, dict):
        item = [item]
    if not isinstance(item, list) or (item and not isinstance(item[0], dict)):
        raise KtoApiError("invalid KTO detail item")
    if not item:
        return DetailFetchResult(DetailAcquisitionState.NO_DATA)
    detail = {k: v for k, v in item[0].items() if v not in ("", None) and k != "contentid"}
    if not detail:
        return DetailFetchResult(DetailAcquisitionState.NO_DATA)
    return DetailFetchResult(DetailAcquisitionState.FETCHED, detail)


def parse_item(item: dict) -> dict | None:
    """목록 항목 → UPSERT 파라미터. 숨김·좌표불량은 None (거부 계수용)."""
    if str(item.get("showflag", "1")) == "0":
        return None
    name = (item.get("title") or "").strip()
    cid = (item.get("contentid") or "").strip()
    try:
        lat, lng = float(item["mapy"]), float(item["mapx"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (name and cid and 32.0 <= lat <= 40.0 and 123.0 <= lng <= 133.0):
        return None
    modified = (item.get("modifiedtime") or "")[:8]
    written = None
    if len(modified) == 8 and modified.isdigit():
        try:
            written = date(int(modified[:4]), int(modified[4:6]), int(modified[6:8]))
        except ValueError:
            written = None
    return {
        "source_ref": cid,
        "name": name,
        "kind": KINDS.get(str(item.get("contenttypeid")), "etc"),
        "category3": str(item.get("lclsSystm3") or item.get("contenttypeid") or ""),
        "sido": None,
        "sigungu": None,
        "address": (item.get("addr1") or "").strip() or None,
        "phone": (item.get("tel") or "").strip() or None,
        # 목록엔 없는 필드들. detailCommon2/detailIntro2 를 붙이기 전까지 None 이고,
        # UPSERT 가 COALESCE 라 기존 값이 있으면 덮지 않는다.
        "homepage": None,
        "hours_text": None,
        "closed_days": None,
        "parking": None,
        "indoor": None,
        "outdoor": None,
        "pet": "{}",  # 빈 상세는 기존 상세를 덮지 않는다
        "lat": lat,
        "lng": lng,
        "last_written": written,
        "raw": json.dumps(item, ensure_ascii=False),
        "modified": item.get("modifiedtime") or "",
    }


def source_records(items: list[dict]) -> list[dict]:
    """제품 필터 전 KTO sync-list를 안정 contentid별 shadow record로 만든다."""

    records: dict[str, dict] = {}
    for item in items:
        content_id = str(item.get("contentid") or "").strip()
        if not content_id:
            continue
        if content_id in records:
            records[content_id]["occurrence_count"] += 1
            records[content_id]["listing_raw"] = dict(item)
        else:
            records[content_id] = {
                "record_ref": content_id,
                "source_ref": content_id,
                "listing_raw": dict(item),
                "occurrence_count": 1,
            }
    return list(records.values())


def watermark_of(rows: list[dict]) -> str | None:
    stamps = [r["modified"] for r in rows if r.get("modified")]
    return max(stamps) if stamps else None


async def _missing_detail_refs(session: AsyncSession, limit: int) -> list[str]:
    """shadow 획득 상태가 미시도·실패·legacy unknown인 행만 고른다."""

    return await pending_detail_refs(session, "kto", limit, require_facility=True)


async def _prune_after_listing(
    session: AsyncSession, mode: str, observed_at: datetime
) -> tuple[int, int]:
    """제품은 full에서만, 전량 관측한 shadow는 성공한 매 실행마다 정리한다."""

    product_pruned = await prune_unseen(session, "kto", observed_at) if mode == "full" else 0
    source_pruned = await prune_source_records(session, "kto", observed_at)
    return product_pruned, source_pruned


async def _run(mode: str, details: int) -> None:
    synced_at = datetime.now(UTC)
    async with SessionLocal() as session:
        prior = (
            await session.execute(
                text("SELECT watermark FROM ingest_state WHERE source = :s"), {"s": SOURCE}
            )
        ).scalar_one_or_none()

        async with httpx.AsyncClient(timeout=20.0) as client:
            items = await fetch_sync_list(client)
            all_rows = [r for r in (parse_item(x) for x in items) if r is not None]
            if not all_rows:
                raise SystemExit("refusing empty KTO snapshot")
            fetched = len(all_rows)
            raw_records = source_records(items)
            snapshot = synced_at.date().isoformat()
            source_stored = await upsert_source_records(
                session,
                "kto",
                raw_records,
                snapshot,
                synced_at,
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
                detail_version_field="modifiedtime",
            )
            # 목록 version 변경·실패·no-data로 source payload를 신뢰할 수 없게 된 순간
            # legacy facility가 예전 상세와 판정을 계속 서비스하지 않도록 같은 트랜잭션에서 비운다.
            detail_invalidated = await invalidate_pet_details_without_source_payload(
                session, "kto"
            )

            rows = all_rows
            if mode == "incremental" and prior:
                rows = [r for r in rows if r["modified"] > prior]

            watermark = watermark_of(rows) or prior
            for row in rows:
                row.pop("modified", None)

            stored = await upsert_rows(
                session,
                "kto",
                rows,
                snapshot,
                synced_at,
                preserve_empty_pet=True,
            )
            # 제품 rows는 증분 필터를 거쳤지만 raw_records는 매번 전체 목록을 관측했다.
            pruned, source_pruned = await _prune_after_listing(session, mode, synced_at)

            got_details = 0
            detail_no_data = 0
            detail_failed = 0
            if details > 0:
                await session.flush()
                for ref in await _missing_detail_refs(session, details):
                    attempted_at = datetime.now(UTC)
                    try:
                        result = await fetch_pet_detail(client, ref)
                    except (httpx.HTTPError, KtoApiError):
                        detail_failed += await record_detail_result(
                            session,
                            "kto",
                            ref,
                            DetailAcquisitionState.FETCH_FAILED,
                            attempted_at,
                        )
                        continue
                    await record_detail_result(
                        session,
                        "kto",
                        ref,
                        result.state,
                        attempted_at,
                        result.detail,
                    )
                    if result.state is DetailAcquisitionState.FETCHED:
                        got_details += await update_pet_detail(
                            session,
                            "kto",
                            ref,
                            json.dumps(result.detail, ensure_ascii=False),
                        )
                    elif result.state is DetailAcquisitionState.NO_DATA:
                        detail_no_data += 1
                    else:
                        detail_failed += 1
                    await asyncio.sleep(0.15)

        linked = await rebuild_links(session)
        if watermark:
            await session.execute(
                text("""
                    INSERT INTO ingest_state (source, watermark, updated_at)
                    VALUES (:source, :watermark, now())
                    ON CONFLICT (source) DO UPDATE SET watermark = :watermark, updated_at = now()
                """),
                {"source": SOURCE, "watermark": watermark},
            )
        await session.commit()

    print(
        json.dumps(
            {
                "source": SOURCE,
                "mode": mode,
                "since": prior,
                "received": len(items),
                "usable": fetched,
                "applied": stored,
                "pruned": pruned,
                "source_stored": source_stored,
                "source_pruned": source_pruned,
                "detail_invalidated": detail_invalidated,
                "pet_details": got_details,
                "detail_no_data": detail_no_data,
                "detail_failed": detail_failed,
                "watermark": watermark,
                **linked,
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="관광공사 반려동물 동반여행을 facility에 적재")
    parser.add_argument("--mode", choices=("incremental", "full"), default="incremental")
    parser.add_argument(
        "--details",
        type=int,
        default=0,
        help="detailPetTour2 보강 상한 (건당 1호출 — 일일 트래픽 주의)",
    )
    args = parser.parse_args()
    if not settings.kto_service_key:
        raise SystemExit("DAENGS_KTO_SERVICE_KEY is required")
    asyncio.run(_run(args.mode, args.details))


if __name__ == "__main__":
    main()
