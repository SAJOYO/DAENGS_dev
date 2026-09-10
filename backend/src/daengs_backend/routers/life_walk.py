"""`/life/walk-conditions` 프록시 (D-068).

**`DAENGS_REALTIME_URL` 이 있을 때만 등록된다.** 비어 있으면 `main.py` 가 예전처럼
`daengs_life` 의 라우터를 그대로 등록하므로, 이 파일은 그 환경에서 아예 안 쓰인다.

**상태 코드와 본문을 손대지 않는다.** 200 도 503 도 422 도 그대로 넘긴다 —
503 본문에는 어느 출처가 죽었는지가 들어 있고 프런트가 그것을 읽는다.

인증은 여기 없다. `main.py` 가 등록 시점에 건다 — `daengs_life` 쪽 라우터와 **같은 자리,
같은 의존성**이라 앱 회원·관리자 판정이 안 갈린다.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from daengs_backend.config import settings
from daengs_backend.services import realtime_client

router = APIRouter(tags=["Life · 산책 적합도"])


@router.get("/life/walk-conditions", summary="산책 적합도 (실시간 서비스로 전달)")
def get_walk(
    response: Response,
    lat: float = Query(..., ge=33.0, le=39.0, description="위도 (WGS84)"),
    lon: float = Query(..., ge=124.0, le=132.0, description="경도 (WGS84)"),
) -> Any:
    try:
        code, payload = realtime_client.get_walk(lat, lon, base_url=settings.realtime_url)
    except realtime_client.RealtimeUnavailable as exc:
        # 전송 실패는 **우리 쪽 저하**다. 실시간 서비스가 낸 503(판정 불가)과 갈라 둔다 —
        # 둘을 같은 코드로 주면 "출처가 죽었나 서비스가 죽었나"를 화면에서 못 가른다.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {"code": "realtime_unavailable", "message": f"실시간 서비스에 닿지 못했습니다: {exc}"},
        ) from exc
    response.status_code = code
    return payload


__all__ = ["router"]
