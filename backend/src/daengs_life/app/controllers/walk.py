"""`GET /life/walk-conditions` (RT-001 ⑥ · RAG-027 의 C 층).

**로직이 없다.** 좌표를 받고, 서비스를 부르고, 그대로 돌려준다. 판정도 조립도 여기 없다 —
그것이 RAG-027 의 유일한 강제 규칙이고, 이 파일이 그 규칙의 본보기다.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from daengs_life.app.deps import get_cache, get_now
from daengs_life.app.dto.walk import WalkOut
from daengs_life.app.services import walk as service
from daengs_life.realtime.cache import Cache
from daengs_life.realtime.geo import LatLon

router = APIRouter(tags=["Life · 산책 적합도"])


@router.get("/life/walk-conditions", response_model=WalkOut, response_model_by_alias=True,
            summary="산책 적합도 — 지금 + T+24h 타임라인 + 권장 구간")
def get_walk(
    lat: float = Query(..., ge=33.0, le=39.0, description="위도 (WGS84)"),
    lon: float = Query(..., ge=124.0, le=132.0, description="경도 (WGS84)"),
    cache: Cache = Depends(get_cache),
    now: datetime = Depends(get_now),
) -> WalkOut:
    """`?lat=37.4979&lon=127.0276`.

    위경도 범위를 여기서 막는 이유 — 남한 밖 좌표는 기상청 격자로 변환은 되지만 **API 가
    빈 응답을 준다.** 그러면 "판정 불가"가 되는데, 원인이 좌표라는 것을 사용자가 알 수 없다.
    경계에서 400 으로 끊는 편이 정직하다.
    """
    result = service.walk(LatLon(lat, lon), now, cache=cache)
    if result.now.grade == "unknown":
        # ⑤-a — 기상청 격자가 통째로 없으면 판정 자체를 안 낸다. 200 으로 "모른다"를 주면
        # 클라이언트가 정상 응답으로 다룬다. 다만 **응답 본문은 그대로 실어** 어느 출처가
        # 죽었는지(`sources`) 보이게 한다
        raise HTTPException(status_code=503, detail=result.model_dump(mode="json", by_alias=True))
    return result


__all__ = ["router"]
