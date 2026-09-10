"""Walk 공간 일기 조회용 DAO. 선택 의미와 집계 판단은 service에 둡니다."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, Select, cast, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import (
    Walk,
    WalkAnalysis,
    WalkCapsule,
    WalkCellophaneSheet,
    WalkPet,
)
from daengs_walk.spatial_diary import DIARY_CALENDAR_TIMEZONE


@dataclass(frozen=True)
class SpatialDiaryIndexRow:
    analysis_id: uuid.UUID
    walk_id: uuid.UUID
    started_at: datetime
    capsule_version: int
    context_version: int
    trail_context: dict[str, Any]
    sheet_schema_version: int | None
    paint_version: int | None
    grid_version: str | None
    radius_u: float | None
    profile: str | None
    profile_fp: str | None
    sample_step_m: float | None
    paint_fp: str | None
    cell_count: int | None


async def count_capsules_for_pet(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
) -> int:
    return int((await session.scalar(capsule_count_statement(app_user_id, pet_id))) or 0)


def capsule_count_statement(app_user_id: uuid.UUID, pet_id: uuid.UUID) -> Select:
    """재분석 Capsule이 아니라 공간 일기 분모가 될 고유 Walk 수를 셉니다."""
    return (
        select(func.count(func.distinct(Walk.id)))
        .select_from(WalkPet)
        .join(Walk, Walk.id == WalkPet.walk_id)
        .join(WalkAnalysis, WalkAnalysis.walk_id == Walk.id)
        .join(WalkCapsule, WalkCapsule.analysis_id == WalkAnalysis.id)
        .where(Walk.app_user_id == app_user_id, WalkPet.pet_id == pet_id)
    )


def capsule_index_statement(
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID | None,
    *,
    since: date | None,
    until: date | None,
    limit: int,
    walk_ids: tuple[uuid.UUID, ...] | None = None,
) -> Select:
    if pet_id is None and walk_ids is None:
        raise ValueError("a pet or explicit walk IDs are required")
    local_day = cast(func.timezone(DIARY_CALENDAR_TIMEZONE, Walk.started_at), Date)
    representative_rank = func.row_number().over(
        partition_by=Walk.id,
        order_by=(
            WalkCapsule.sealed_at.desc(),
            WalkAnalysis.derived_at.desc(),
            WalkAnalysis.id.desc(),
            WalkCellophaneSheet.derived_at.desc().nulls_last(),
            WalkCellophaneSheet.paint_fp.desc().nulls_last(),
        ),
    )
    ranked = (
        select(
            WalkAnalysis.id.label("analysis_id"),
            Walk.id.label("walk_id"),
            Walk.started_at.label("started_at"),
            WalkCapsule.capsule_version.label("capsule_version"),
            WalkCapsule.context_version.label("context_version"),
            WalkCapsule.trail_context.label("trail_context"),
            WalkCellophaneSheet.sheet_schema_version.label("sheet_schema_version"),
            WalkCellophaneSheet.paint_version.label("paint_version"),
            WalkCellophaneSheet.grid_version.label("grid_version"),
            WalkCellophaneSheet.radius_u.label("radius_u"),
            WalkCellophaneSheet.profile.label("profile"),
            WalkCellophaneSheet.profile_fp.label("profile_fp"),
            WalkCellophaneSheet.sample_step_m.label("sample_step_m"),
            WalkCellophaneSheet.paint_fp.label("paint_fp"),
            WalkCellophaneSheet.cell_count.label("cell_count"),
            representative_rank.label("representative_rank"),
        )
        .select_from(Walk)
        .join(WalkAnalysis, WalkAnalysis.walk_id == Walk.id)
        .join(WalkCapsule, WalkCapsule.analysis_id == WalkAnalysis.id)
        .outerjoin(
            WalkCellophaneSheet,
            WalkCellophaneSheet.analysis_id == WalkAnalysis.id,
        )
        .where(Walk.app_user_id == app_user_id)
    )
    if pet_id is not None:
        ranked = ranked.join(WalkPet, WalkPet.walk_id == Walk.id).where(WalkPet.pet_id == pet_id)
    if walk_ids is not None:
        ranked = ranked.where(Walk.id.in_(walk_ids))
    if since is not None:
        ranked = ranked.where(local_day >= since)
    if until is not None:
        ranked = ranked.where(local_day <= until)
    ranked = ranked.subquery("ranked_spatial_diary_capsules")

    return (
        select(
            ranked.c.analysis_id,
            ranked.c.walk_id,
            ranked.c.started_at,
            ranked.c.capsule_version,
            ranked.c.context_version,
            ranked.c.trail_context,
            ranked.c.sheet_schema_version,
            ranked.c.paint_version,
            ranked.c.grid_version,
            ranked.c.radius_u,
            ranked.c.profile,
            ranked.c.profile_fp,
            ranked.c.sample_step_m,
            ranked.c.paint_fp,
            ranked.c.cell_count,
        )
        .where(ranked.c.representative_rank == 1)
        .order_by(
            ranked.c.started_at,
            ranked.c.walk_id,
            ranked.c.analysis_id,
            ranked.c.paint_fp,
        )
        .limit(limit)
    )


async def list_capsule_index(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    since: date | None,
    until: date | None,
    limit: int,
) -> list[SpatialDiaryIndexRow]:
    rows = (
        await session.execute(
            capsule_index_statement(
                app_user_id,
                pet_id,
                since=since,
                until=until,
                limit=limit,
            )
        )
    ).all()
    return [SpatialDiaryIndexRow(*row) for row in rows]


async def list_owned_record_ids(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    client_session_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, uuid.UUID]:
    """GPS·반려견 관계를 로딩하지 않고 기기 ID와 내 서버 Walk ID만 연결합니다."""
    rows = await session.execute(
        select(Walk.client_session_id, Walk.id).where(
            Walk.app_user_id == app_user_id,
            Walk.client_session_id.in_(client_session_ids),
        )
    )
    return dict(rows.all())


async def list_record_capsule_index(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    walk_ids: tuple[uuid.UUID, ...],
) -> list[SpatialDiaryIndexRow]:
    if not walk_ids:
        return []
    rows = await session.execute(
        capsule_index_statement(
            app_user_id,
            None,
            since=None,
            until=None,
            limit=len(walk_ids),
            walk_ids=walk_ids,
        )
    )
    return [SpatialDiaryIndexRow(*row) for row in rows.all()]


async def list_cellophane_sheets(
    session: AsyncSession,
    keys: list[tuple[uuid.UUID, str]],
) -> list[WalkCellophaneSheet]:
    if not keys:
        return []
    stmt = (
        select(WalkCellophaneSheet)
        .where(
            tuple_(
                WalkCellophaneSheet.analysis_id,
                WalkCellophaneSheet.paint_fp,
            ).in_(keys)
        )
        .order_by(WalkCellophaneSheet.analysis_id, WalkCellophaneSheet.paint_fp)
    )
    return list(await session.scalars(stmt))
