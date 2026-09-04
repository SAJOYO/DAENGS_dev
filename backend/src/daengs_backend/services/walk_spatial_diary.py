"""Walk가 소유한 Capsule과 Cellophane을 한 공간 일기 View로 조립합니다."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import walk_spatial_diary as diary_repo
from daengs_backend.services.walk_analysis import decode_stored_cellophane
from daengs_walk.capsule import CAPSULE_VERSION, TrailContextSnapshot
from daengs_walk.cellophane import CANONICAL_PAINT_SPEC, Cellophane, PaintSpec
from daengs_walk.spatial_diary import (
    MixedPaintGenerationError,
    SpatialDiaryViewReceipt,
    SpatialDiaryViewSpec,
    SpatialField,
    aggregate_spatial_field,
    build_view_receipt,
    context_is_known,
    matches_walk_selector,
)

MAX_CANDIDATE_CAPSULES = 2_000
MAX_SELECTED_CAPSULES = 400
MAX_RAW_CELLS = 100_000
MAX_RESULT_CELLS = 5_000


class SpatialDiaryPetNotFoundError(LookupError):
    """내 강아지가 아니거나 존재하지 않습니다."""


class IncompleteSpatialDiaryCapsuleError(RuntimeError):
    """봉인됐다고 선언한 Capsule의 필수 원판이 없거나 변조됐습니다."""


class SpatialDiaryViewTooLargeError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CapsuleIndex:
    analysis_id: uuid.UUID
    walk_id: uuid.UUID
    started_at: datetime
    context: TrailContextSnapshot
    paint_spec: PaintSpec
    cell_count: int


@dataclass(frozen=True)
class SpatialDiaryViewResult:
    spec: SpatialDiaryViewSpec
    field: SpatialField
    receipt: SpatialDiaryViewReceipt


async def query_view(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    spec: SpatialDiaryViewSpec,
    *,
    view_as_of: datetime | None = None,
) -> SpatialDiaryViewResult:
    """한 read-only repeatable-read 세션 안에서 View 전체를 조립합니다."""

    selector = spec.walk_selector
    if await pet_repo.get_owned(session, app_user_id, selector.pet_id) is None:
        raise SpatialDiaryPetNotFoundError

    total_capsules = await diary_repo.count_capsules_for_pet(
        session,
        app_user_id,
        selector.pet_id,
    )
    stored_index = await diary_repo.list_capsule_index(
        session,
        app_user_id,
        selector.pet_id,
        since=selector.since,
        until=selector.until,
        limit=MAX_CANDIDATE_CAPSULES + 1,
    )
    if len(stored_index) > MAX_CANDIDATE_CAPSULES:
        raise SpatialDiaryViewTooLargeError(
            "spatial_diary_candidate_limit",
            f"후보 Capsule은 최대 {MAX_CANDIDATE_CAPSULES}개입니다. 기간을 좁혀 주세요.",
        )

    candidates = tuple(_decode_index(row) for row in stored_index)
    selected = tuple(
        item
        for item in candidates
        if matches_walk_selector(
            selector,
            started_at=item.started_at,
            context=item.context,
        )
    )
    if len(selected) > MAX_SELECTED_CAPSULES:
        raise SpatialDiaryViewTooLargeError(
            "spatial_diary_selected_limit",
            f"선택된 Capsule은 최대 {MAX_SELECTED_CAPSULES}개입니다. 필터를 좁혀 주세요.",
        )

    paint_fps = {item.paint_spec.fingerprint for item in selected}
    if len(paint_fps) > 1:
        raise MixedPaintGenerationError(
            f"spatial diary view cannot mix paint generations: {sorted(paint_fps)}"
        )
    paint_spec = selected[0].paint_spec if selected else CANONICAL_PAINT_SPEC

    raw_cells = sum(item.cell_count for item in selected)
    if raw_cells > MAX_RAW_CELLS:
        raise SpatialDiaryViewTooLargeError(
            "spatial_diary_raw_cell_limit",
            f"선택된 원시 Cell은 최대 {MAX_RAW_CELLS}개입니다. 필터를 좁혀 주세요.",
        )

    sheets = await _load_selected_sheets(session, selected)
    field = aggregate_spatial_field(sheets, spec.field_metric, paint_spec=paint_spec)
    if len(field.values) > MAX_RESULT_CELLS:
        raise SpatialDiaryViewTooLargeError(
            "spatial_diary_result_cell_limit",
            f"결과 Cell은 최대 {MAX_RESULT_CELLS}개입니다. 필터를 좁혀 주세요.",
        )

    known = sum(context_is_known(item.context, selector) for item in selected)
    receipt = build_view_receipt(
        spec,
        field,
        view_as_of=view_as_of or datetime.now(UTC),
        total_capsules=total_capsules,
        context_known_count=known,
    )
    return SpatialDiaryViewResult(spec=spec, field=field, receipt=receipt)


def _decode_index(row: diary_repo.SpatialDiaryIndexRow) -> CapsuleIndex:
    required_sheet_values = (
        row.sheet_schema_version,
        row.paint_version,
        row.grid_version,
        row.radius_u,
        row.profile,
        row.profile_fp,
        row.sample_step_m,
        row.paint_fp,
        row.cell_count,
    )
    if row.capsule_version != CAPSULE_VERSION or any(
        value is None for value in required_sheet_values
    ):
        raise IncompleteSpatialDiaryCapsuleError(
            "봉인된 Capsule의 지원 세대 또는 Cellophane metadata가 완전하지 않습니다."
        )
    try:
        context = TrailContextSnapshot.model_validate(row.trail_context)
        paint_spec = PaintSpec(
            paint_version=row.paint_version,
            grid_version=row.grid_version,
            radius_u=row.radius_u,
            profile_name=row.profile,
            profile_fp=row.profile_fp,
            sample_step_m=row.sample_step_m,
        )
    except (TypeError, ValueError) as exc:
        raise IncompleteSpatialDiaryCapsuleError(
            "봉인된 Capsule의 context 또는 Paint metadata를 읽을 수 없습니다."
        ) from exc
    if (
        context.walk_id != row.walk_id
        or context.walked_at != row.started_at
        or context.context_version != row.context_version
        or paint_spec.fingerprint != row.paint_fp
        or row.cell_count < 0
    ):
        raise IncompleteSpatialDiaryCapsuleError(
            "봉인된 Capsule의 identity와 저장 metadata가 일치하지 않습니다."
        )
    return CapsuleIndex(
        analysis_id=row.analysis_id,
        walk_id=row.walk_id,
        started_at=row.started_at,
        context=context,
        paint_spec=paint_spec,
        cell_count=row.cell_count,
    )


async def _load_selected_sheets(
    session: AsyncSession,
    selected: tuple[CapsuleIndex, ...],
) -> tuple[Cellophane, ...]:
    keys = [(item.analysis_id, item.paint_spec.fingerprint) for item in selected]
    stored = await diary_repo.list_cellophane_sheets(session, keys)
    by_key = {(item.analysis_id, item.paint_fp): item for item in stored}
    if set(by_key) != set(keys):
        raise IncompleteSpatialDiaryCapsuleError(
            "봉인된 Capsule의 Cellophane 원판을 모두 찾을 수 없습니다."
        )

    decoded = []
    for item in selected:
        key = (item.analysis_id, item.paint_spec.fingerprint)
        try:
            sheet = decode_stored_cellophane(by_key[key])
        except (TypeError, ValueError) as exc:
            raise IncompleteSpatialDiaryCapsuleError(
                "봉인된 Cellophane 원판을 검증할 수 없습니다."
            ) from exc
        if (
            sheet.walk_id != item.walk_id
            or sheet.at != item.started_at
            or sheet.paint_fp != item.paint_spec.fingerprint
        ):
            raise IncompleteSpatialDiaryCapsuleError(
                "Cellophane 원판이 선택된 Capsule identity와 일치하지 않습니다."
            )
        decoded.append(sheet)
    return tuple(decoded)
