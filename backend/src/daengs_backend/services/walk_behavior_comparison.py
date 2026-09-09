"""같은 snapshot에서 기준 산책 A와 행동 기록을 가진 부분집합 B를 조립합니다."""

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.repositories import walk_behavior_comparison as repo
from daengs_backend.schemas.walk_behavior_comparison import (
    BehaviorComparisonEvidence,
    BehaviorComparisonRequest,
    BehaviorComparisonSummary,
)
from daengs_backend.services import walk_spatial_diary as spatial
from daengs_backend.services.walk_analysis import cellophane_sheet_fingerprint, encode_cellophane
from daengs_backend.services.walk_entry_v2 import digest, require_enabled, response
from daengs_walk.spatial_diary import (
    DIARY_CALENDAR_TIMEZONE,
    SpatialField,
    aggregate_spatial_field,
    selector_fingerprint,
)

MAX_BEHAVIOR_EVIDENCE = 2_000
_DIARY_ZONE = ZoneInfo(DIARY_CALENDAR_TIMEZONE)


@dataclass(frozen=True)
class BehaviorComparisonResult:
    baseline_walk_ids: tuple[uuid.UUID, ...]
    matching_walk_ids: tuple[uuid.UUID, ...]
    baseline_field: SpatialField
    matching_field: SpatialField
    summary: BehaviorComparisonSummary
    evidence: tuple[BehaviorComparisonEvidence, ...]
    source_revision: str
    view_as_of: datetime


async def query_comparison(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    request: BehaviorComparisonRequest,
    *,
    view_as_of: datetime | None = None,
) -> BehaviorComparisonResult:
    """조회만 합니다. 위치 불확실성은 핀 표시의 근거이지 산책 선택 조건이 아닙니다."""
    require_enabled()
    spec = request.spatial_spec()
    sources = await spatial.load_view_sources(session, app_user_id, spec)
    baseline = tuple(sheet for sheet in sources.sheets if math.fsum(sheet.occupancy.values()) > 0)
    baseline_ids = tuple(sheet.walk_id for sheet in baseline)
    baseline_field = aggregate_spatial_field(
        baseline,
        "walk_utilization",
        paint_spec=sources.paint_spec,
    )
    spatial.validate_result_size(baseline_field)

    rows = await repo.list_behavior_entries(
        session,
        app_user_id,
        request.walk_selector.pet_id,
        baseline_ids,
        request.behavior_code,
        limit=MAX_BEHAVIOR_EVIDENCE + 1,
    )
    if len(rows) > MAX_BEHAVIOR_EVIDENCE:
        raise spatial.SpatialDiaryViewTooLargeError(
            "behavior_comparison_evidence_limit",
            f"행동 근거는 최대 {MAX_BEHAVIOR_EVIDENCE}개입니다. 기간을 좁혀 주세요.",
        )
    evidence = tuple(
        sorted(
            (_evidence(row) for row in rows),
            key=lambda item: (item.recorded_at, item.walk_id, item.entry_id),
        )
    )
    matching_ids = {item.walk_id for item in evidence}
    matching = tuple(sheet for sheet in baseline if sheet.walk_id in matching_ids)
    matching_field = aggregate_spatial_field(
        matching,
        "walk_utilization",
        paint_spec=sources.paint_spec,
    )
    spatial.validate_result_size(matching_field)
    summary = BehaviorComparisonSummary(
        selected_walk_count=len(sources.selected),
        excluded_empty_walk_count=len(sources.selected) - len(baseline),
        entry_count=len(evidence),
        recorded_day_count=len(
            {item.recorded_at.astimezone(_DIARY_ZONE).date() for item in evidence}
        ),
        unlocated_entry_count=sum(
            item.pin is None or item.pin.get("point") is None for item in evidence
        ),
    )
    # Include even empty selected sheets: changing which walks were excluded changes this result.
    # A digest identifies the current sources; it is not a backup of deleted source data.
    source_revision = digest(
        {
            "spec": request.model_dump(mode="json"),
            "spatial_selector_fingerprint": selector_fingerprint(spec),
            "paint_fp": sources.paint_spec.fingerprint,
            "sources": [
                {
                    "analysis_id": str(index.analysis_id),
                    "walk_id": str(index.walk_id),
                    "context": index.context.model_dump(mode="json"),
                    "paint_fp": sheet.paint_fp,
                    "sheet_fingerprint": cellophane_sheet_fingerprint(encode_cellophane(sheet)),
                }
                for index, sheet in zip(sources.selected, sources.sheets, strict=True)
            ],
            "baseline_walk_ids": [str(walk_id) for walk_id in baseline_ids],
            "evidence": [item.model_dump(mode="json") for item in evidence],
        }
    )
    return BehaviorComparisonResult(
        baseline_walk_ids=baseline_ids,
        matching_walk_ids=tuple(sheet.walk_id for sheet in matching),
        baseline_field=baseline_field,
        matching_field=matching_field,
        summary=summary,
        evidence=evidence,
        source_revision=source_revision,
        view_as_of=view_as_of or datetime.now(UTC),
    )


def _evidence(row: repo.BehaviorEntryRow) -> BehaviorComparisonEvidence:
    current = response(row.entry, row.pin)
    return BehaviorComparisonEvidence(
        **current["content"],
        entry_id=row.entry.id,
        entry_revision=row.entry.revision,
        walk_id=row.entry.walk_id,
        client_session_id=row.client_session_id,
        pin=current["pin"],
        pin_revision=current["pin_revision"],
    )
