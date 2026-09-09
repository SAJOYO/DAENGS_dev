"""앱 회원용 Walk 공간 일기 읽기 HTTP 경계."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_snapshot_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.walk_behavior_comparison import (
    BehaviorComparisonCohort,
    BehaviorComparisonReceipt,
    BehaviorComparisonRequest,
    BehaviorComparisonResponse,
)
from daengs_backend.schemas.walk_spatial_diary import (
    SpatialDiaryFieldCellResponse,
    SpatialDiaryFieldResponse,
    SpatialDiaryProjectionResponse,
    SpatialDiaryReceiptResponse,
    SpatialDiaryViewRequest,
    SpatialDiaryViewResponse,
)
from daengs_backend.services import walk_behavior_comparison as comparison_service
from daengs_backend.services import walk_spatial_diary as diary_service
from daengs_backend.services.walk_entry import EntryNotFound
from daengs_walk.cellophane import PaintSpec
from daengs_walk.spatial_diary import (
    DuplicateWalkInViewError,
    MixedPaintGenerationError,
    SpatialField,
    selector_fingerprint,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app/walks/spatial-diary", tags=["walks"])


@router.post("/views/query", response_model=SpatialDiaryViewResponse)
async def query_spatial_diary_view(
    body: SpatialDiaryViewRequest,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_snapshot_session)],
) -> SpatialDiaryViewResponse:
    """내 강아지의 봉인된 산책을 조건별 공간 field로 읽습니다."""
    spec = body.to_spec()
    try:
        result = await diary_service.query_view(
            session,
            user.app_user_id,
            spec,
        )
    except diary_service.SpatialDiaryPetNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "공간 일기를 조회할 강아지를 찾을 수 없습니다.",
        ) from None
    except diary_service.SpatialDiaryViewTooLargeError as exc:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": exc.code, "message": exc.detail},
        ) from None
    except MixedPaintGenerationError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "spatial_diary_mixed_paint",
                "message": "서로 다른 지도 계산 세대는 한 화면에 합칠 수 없습니다.",
            },
        ) from None
    except DuplicateWalkInViewError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "spatial_diary_duplicate_walk",
                "message": "같은 산책이 공간 일기 분모에 두 번 포함됐습니다.",
            },
        ) from None
    except diary_service.IncompleteSpatialDiaryCapsuleError:
        logger.exception(
            "spatial diary capsule incomplete (app_user=%s, pet=%s, selector=%s)",
            user.app_user_id,
            spec.walk_selector.pet_id,
            selector_fingerprint(spec),
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "spatial_diary_capsule_incomplete",
                "message": "봉인된 산책 원판을 읽을 수 없습니다.",
            },
        ) from None
    return _to_response(body, result)


def _projection_response(paint: PaintSpec) -> SpatialDiaryProjectionResponse:
    return SpatialDiaryProjectionResponse(
        paint_version=paint.paint_version,
        grid_version=paint.grid_version,
        radius_u=paint.radius_u,
        profile_name=paint.profile_name,
        profile_fp=paint.profile_fp,
        sample_step_m=paint.sample_step_m,
        paint_fp=paint.fingerprint,
    )


def _field_response(field: SpatialField) -> SpatialDiaryFieldResponse:
    return SpatialDiaryFieldResponse(
        metric=field.metric,
        unit=field.unit,
        normalization=field.normalization,
        denominator=field.denominator,
        cells=tuple(
            SpatialDiaryFieldCellResponse(
                q=q,
                r=r,
                value=value,
                numerator=field.numerators[(q, r)],
            )
            for (q, r), value in field.values.items()
        ),
    )


def _to_response(
    request: SpatialDiaryViewRequest,
    result: diary_service.SpatialDiaryViewResult,
) -> SpatialDiaryViewResponse:
    return SpatialDiaryViewResponse(
        spec=request,
        projection=_projection_response(result.field.paint_spec),
        field=_field_response(result.field),
        receipt=SpatialDiaryReceiptResponse(**result.receipt.model_dump()),
    )


@router.post("/behavior-comparisons/query", response_model=BehaviorComparisonResponse)
async def query_behavior_comparison(
    body: BehaviorComparisonRequest,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_snapshot_session)],
) -> BehaviorComparisonResponse:
    """기준 산책과 그 안에서 행동 기록이 있는 산책의 공간 분포를 비교합니다."""
    try:
        result = await comparison_service.query_comparison(session, user.app_user_id, body)
    except EntryNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "행동 비교를 사용할 수 없습니다.") from None
    except diary_service.SpatialDiaryPetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None
    except diary_service.SpatialDiaryViewTooLargeError as exc:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": exc.code, "message": exc.detail},
        ) from None
    except MixedPaintGenerationError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "spatial_diary_mixed_paint",
                "message": "서로 다른 지도 계산 세대는 한 화면에 합칠 수 없습니다.",
            },
        ) from None
    except DuplicateWalkInViewError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "spatial_diary_duplicate_walk",
                "message": "같은 산책이 공간 일기 분모에 두 번 포함됐습니다.",
            },
        ) from None
    except diary_service.IncompleteSpatialDiaryCapsuleError:
        logger.exception(
            "behavior comparison capsule incomplete (app_user=%s, pet=%s)",
            user.app_user_id,
            body.walk_selector.pet_id,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "spatial_diary_capsule_incomplete",
                "message": "봉인된 산책 원판을 읽을 수 없습니다.",
            },
        ) from None
    return BehaviorComparisonResponse(
        spec=body,
        projection=_projection_response(result.baseline_field.paint_spec),
        baseline=BehaviorComparisonCohort(
            walk_ids=result.baseline_walk_ids,
            field=_field_response(result.baseline_field),
        ),
        matching=BehaviorComparisonCohort(
            walk_ids=result.matching_walk_ids,
            field=_field_response(result.matching_field),
        ),
        summary=result.summary,
        evidence=result.evidence,
        receipt=BehaviorComparisonReceipt(
            source_revision=result.source_revision,
            view_as_of=result.view_as_of,
            paint_fp=result.baseline_field.paint_fp,
        ),
    )
