"""shadow source record를 후보 단위 fact bundle로 읽는 runtime bridge."""

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.place.contracts import PlaceRef
from daengs_place.place.source_facts.bundle import (
    CandidateFactBundle,
    SourceFactKey,
    SourceFactSource,
    SourceFactVariant,
    build_candidate_fact_bundle,
)
from daengs_place.place.source_facts.contract import ProjectionIssue, SourceFactProjection
from daengs_place.place.source_facts.kcisa import PARSER_VERSION as KCISA_PARSER_VERSION
from daengs_place.place.source_facts.kcisa import project_kcisa
from daengs_place.place.source_facts.kto import PARSER_VERSION as KTO_PARSER_VERSION
from daengs_place.place.source_facts.kto import project_kto
from daengs_place.place.source_facts.states import (
    DetailAcquisitionState,
    ProjectionState,
    acquisition_fact_state,
)

MAX_BUNDLE_CANDIDATES = 1000

_READ_SOURCE_RECORDS = text("""
WITH requested AS (
    SELECT DISTINCT source, source_ref
    FROM jsonb_to_recordset(CAST(:keys AS jsonb))
         AS candidate(source text, source_ref text)
)
SELECT record.source, record.record_ref, record.source_ref,
       record.listing_raw, record.occurrence_count,
       record.detail_raw, record.detail_state, record.snapshot
FROM requested
JOIN facility_source_record record
  ON record.source = requested.source
 AND record.source_ref = requested.source_ref
ORDER BY record.source, record.source_ref, record.record_ref
""")


@dataclass(frozen=True)
class _SourceRecord:
    source: SourceFactSource
    record_ref: str
    source_ref: str
    listing_raw: dict
    occurrence_count: int
    detail_raw: dict | None
    detail_state: DetailAcquisitionState
    snapshot: str


def source_fact_key(ref: PlaceRef) -> SourceFactKey | None:
    """canonical Place ref가 shadow fact를 지원할 때만 내부 키로 옮긴다."""

    if ref.source not in {"kcisa", "kto"}:
        return None
    return SourceFactKey(source=cast(SourceFactSource, ref.source), source_ref=ref.ref)


def _failed_projection(
    source: SourceFactSource,
    exc: AttributeError | TypeError | ValueError,
) -> SourceFactProjection:
    parser_version = KCISA_PARSER_VERSION if source == "kcisa" else KTO_PARSER_VERSION
    return SourceFactProjection(
        source=source,
        parser_version=parser_version,
        state=ProjectionState.FAILED,
        issues=(
            ProjectionIssue(
                code="projection_failed",
                detail=f"{type(exc).__name__}: {exc}",
            ),
        ),
    )


def _project(record: _SourceRecord) -> SourceFactProjection:
    try:
        if record.source == "kcisa":
            return project_kcisa(record.listing_raw)
        return project_kto(
            record.listing_raw,
            record.detail_raw,
            detail_state=acquisition_fact_state(record.detail_state),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        return _failed_projection(record.source, exc)


def _variants(records: list[_SourceRecord]) -> list[SourceFactVariant]:
    return [
        SourceFactVariant(
            source_ref=record.source_ref,
            record_ref=record.record_ref,
            occurrence_count=record.occurrence_count,
            snapshot=record.snapshot,
            detail_state=record.detail_state,
            projection=_project(record),
        )
        for record in sorted(records, key=lambda item: item.record_ref)
    ]

def _validate_keys(keys: list[SourceFactKey]) -> None:
    if len(keys) > MAX_BUNDLE_CANDIDATES:
        raise ValueError(f"at most {MAX_BUNDLE_CANDIDATES} source fact candidates per read")


async def load_candidate_fact_bundles(
    session: AsyncSession,
    keys: list[SourceFactKey],
) -> list[CandidateFactBundle]:
    """최대 1,000개 후보의 shadow records를 한 SQL로 읽고 입력 순서로 돌려준다.

    shadow가 없는 후보도 `missing` bundle로 남긴다. 조용히 drop하면 검색 후보와 fact 결과의
    위치 대응이 깨지고, 미상을 데이터 없음이 아니라 필터 탈락으로 오독하게 된다.
    """

    _validate_keys(keys)
    if not keys:
        return []
    rows = await session.execute(
        _READ_SOURCE_RECORDS,
        {
            "keys": json.dumps(
                [key.model_dump(mode="json") for key in keys],
                ensure_ascii=False,
            )
        },
    )
    records: dict[tuple[str, str], list[_SourceRecord]] = defaultdict(list)
    for row in rows.mappings():
        source = cast(SourceFactSource, row["source"])
        records[(source, row["source_ref"])].append(
            _SourceRecord(
                source=source,
                record_ref=row["record_ref"],
                source_ref=row["source_ref"],
                listing_raw=row["listing_raw"],
                occurrence_count=row["occurrence_count"],
                detail_raw=row["detail_raw"],
                detail_state=DetailAcquisitionState(row["detail_state"]),
                snapshot=row["snapshot"],
            )
        )
    return [
        build_candidate_fact_bundle(
            key,
            _variants(records.get((key.source, key.source_ref), [])),
        )
        for key in keys
    ]
