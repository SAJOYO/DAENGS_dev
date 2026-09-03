"""앱 회원용 Walk 공간 일기 읽기 HTTP 경계."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_snapshot_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.walk_spatial_diary import (
    SpatialDiaryFieldCellResponse,
    SpatialDiaryFieldResponse,
    SpatialDiaryProjectionResponse,
    SpatialDiaryReceiptResponse,
    SpatialDiaryViewRequest,
    SpatialDiaryViewResponse,
)
from daengs_backend.services import walk_spatial_diary as diary_service
from daengs_walk.spatial_diary import (
    DuplicateWalkInViewError,
    MixedPaintGenerationError,
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


def _to_response(
    request: SpatialDiaryViewRequest,
    result: diary_service.SpatialDiaryViewResult,
) -> SpatialDiaryViewResponse:
    field = result.field
    paint = field.paint_spec
    receipt = result.receipt
    return SpatialDiaryViewResponse(
        spec=request,
        projection=SpatialDiaryProjectionResponse(
            paint_version=paint.paint_version,
            grid_version=paint.grid_version,
            radius_u=paint.radius_u,
            profile_name=paint.profile_name,
            profile_fp=paint.profile_fp,
            sample_step_m=paint.sample_step_m,
            paint_fp=paint.fingerprint,
        ),
        field=SpatialDiaryFieldResponse(
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
        ),
        receipt=SpatialDiaryReceiptResponse(**receipt.model_dump()),
    )
