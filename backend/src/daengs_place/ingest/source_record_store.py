"""원천 레코드 shadow 저장소. 제품 facility와 projector 결과를 저장하지 않는다."""

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.place.source_facts.states import DetailAcquisitionState

_UPSERT_LISTING = text("""
INSERT INTO facility_source_record (
    source, record_ref, source_ref, listing_raw, occurrence_count, detail_raw, detail_state,
    snapshot, observed_at, detail_attempted_at, detail_fetched_at
)
VALUES (
    :source, :record_ref, :source_ref, CAST(:listing_raw AS jsonb), :occurrence_count,
    NULL, :detail_state,
    :snapshot, :observed_at, NULL, NULL
)
ON CONFLICT (source, record_ref) DO UPDATE SET
    source_ref = EXCLUDED.source_ref,
    listing_raw = EXCLUDED.listing_raw,
    occurrence_count = EXCLUDED.occurrence_count,
    snapshot = EXCLUDED.snapshot,
    observed_at = EXCLUDED.observed_at,
    detail_raw = CASE
        WHEN :preserve_detail AND (
            CAST(:detail_version_field AS text) IS NULL
            OR (facility_source_record.listing_raw ->> CAST(:detail_version_field AS text))
               IS NOT DISTINCT FROM
               (EXCLUDED.listing_raw ->> CAST(:detail_version_field AS text))
        ) THEN facility_source_record.detail_raw
        ELSE EXCLUDED.detail_raw
    END,
    detail_state = CASE
        WHEN :preserve_detail AND (
            CAST(:detail_version_field AS text) IS NULL
            OR (facility_source_record.listing_raw ->> CAST(:detail_version_field AS text))
               IS NOT DISTINCT FROM
               (EXCLUDED.listing_raw ->> CAST(:detail_version_field AS text))
        ) THEN facility_source_record.detail_state
        ELSE EXCLUDED.detail_state
    END,
    detail_attempted_at = CASE
        WHEN :preserve_detail AND (
            CAST(:detail_version_field AS text) IS NULL
            OR (facility_source_record.listing_raw ->> CAST(:detail_version_field AS text))
               IS NOT DISTINCT FROM
               (EXCLUDED.listing_raw ->> CAST(:detail_version_field AS text))
        ) THEN facility_source_record.detail_attempted_at
        ELSE NULL
    END,
    detail_fetched_at = CASE
        WHEN :preserve_detail AND (
            CAST(:detail_version_field AS text) IS NULL
            OR (facility_source_record.listing_raw ->> CAST(:detail_version_field AS text))
               IS NOT DISTINCT FROM
               (EXCLUDED.listing_raw ->> CAST(:detail_version_field AS text))
        ) THEN facility_source_record.detail_fetched_at
        ELSE NULL
    END
""")


async def upsert_source_records(
    session: AsyncSession,
    source: str,
    records: list[dict],
    snapshot: str,
    observed_at: datetime,
    *,
    detail_state: DetailAcquisitionState,
    preserve_detail: bool,
    detail_version_field: str | None = None,
) -> int:
    """목록/CSV 원문을 UPSERT한다.

    상세를 보존하는 원천도 version 필드가 바뀌면 새 목록에 맞춰 미수집 상태로 되돌린다.
    """

    if detail_state not in {
        DetailAcquisitionState.NOT_APPLICABLE,
        DetailAcquisitionState.NOT_FETCHED,
    }:
        raise ValueError("listing upsert accepts only not_applicable or not_fetched")
    payloads = [
        {
            "source": source,
            "record_ref": record.get("record_ref", record["source_ref"]),
            "source_ref": record["source_ref"],
            "listing_raw": json.dumps(record["listing_raw"], ensure_ascii=False),
            "occurrence_count": record.get("occurrence_count", 1),
            "detail_state": detail_state.value,
            "snapshot": snapshot,
            "observed_at": observed_at,
            "preserve_detail": preserve_detail,
            "detail_version_field": detail_version_field,
        }
        for record in records
    ]
    for start in range(0, len(payloads), 1000):
        await session.execute(_UPSERT_LISTING, payloads[start : start + 1000])
    return len(payloads)


_UPDATE_DETAIL = text("""
UPDATE facility_source_record SET
    detail_raw = CAST(:detail_raw AS jsonb),
    detail_state = :detail_state,
    detail_attempted_at = CAST(:attempted_at AS timestamptz),
    detail_fetched_at = CASE
        WHEN :detail_state = 'fetched' THEN CAST(:attempted_at AS timestamptz)
    END
WHERE source = :source AND record_ref = :record_ref
""")


async def record_detail_result(
    session: AsyncSession,
    source: str,
    source_ref: str,
    state: DetailAcquisitionState,
    attempted_at: datetime,
    detail: dict | None = None,
) -> int:
    """상세 시도 결과를 기록한다. payload와 상태가 어긋나면 DB 전에 거부한다."""

    if state not in {
        DetailAcquisitionState.FETCHED,
        DetailAcquisitionState.NO_DATA,
        DetailAcquisitionState.FETCH_FAILED,
    }:
        raise ValueError("detail result must be fetched, no_data, or fetch_failed")
    if (state is DetailAcquisitionState.FETCHED) != (detail is not None):
        raise ValueError("only fetched detail state may carry a payload")
    result = await session.execute(
        _UPDATE_DETAIL,
        {
            "source": source,
            "record_ref": source_ref,
            "detail_raw": (json.dumps(detail, ensure_ascii=False) if detail is not None else None),
            "detail_state": state.value,
            "attempted_at": attempted_at,
        },
    )
    return result.rowcount

async def pending_detail_refs(
    session: AsyncSession,
    source: str,
    limit: int,
    *,
    require_facility: bool = False,
) -> list[str]:
    """미시도·실패·legacy unknown만 재시도한다. 정상 no-data는 자동 반복하지 않는다."""

    rows = await session.execute(
        text("""
            SELECT record_ref
            FROM facility_source_record
            WHERE source = :source
              AND detail_state IN ('not_fetched', 'fetch_failed', 'unknown')
              AND (
                NOT CAST(:require_facility AS boolean)
                OR EXISTS (
                    SELECT 1 FROM facility
                    WHERE facility.source = facility_source_record.source
                      AND facility.source_ref = facility_source_record.source_ref
                )
              )
            ORDER BY CASE detail_state
                       WHEN 'not_fetched' THEN 0
                       WHEN 'unknown' THEN 1
                       ELSE 2
                     END,
                     detail_attempted_at NULLS FIRST,
                     record_ref
            LIMIT :limit
        """),
        {"source": source, "limit": limit, "require_facility": require_facility},
    )
    return list(rows.scalars())


async def prune_source_records(session: AsyncSession, source: str, observed_at: datetime) -> int:
    """full snapshot에서 이번 관측에 없던 원천 레코드만 제거한다."""

    result = await session.execute(
        text("""
            DELETE FROM facility_source_record
            WHERE source = :source AND observed_at < :observed_at
        """),
        {"source": source, "observed_at": observed_at},
    )
    return result.rowcount
