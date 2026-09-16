"""`/admin/cardimage/*` — 콘솔의 「도감 카드 생성」 탭이 부르는 점검 경로 (#496).

저장하지 않는다. 사진을 받아 카드 PNG 를 base64 로 돌려주고 끝이다 — 앱용 저장 경로는 `/app/ai-cards`.
사진은 요청 본문 원시 바이트다 (이 저장소는 multipart 를 쓰지 않는다 — bridge 업로드와 같은 방식).
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from daengs_backend.core.deps import Perm, Principal, require
from daengs_backend.routers.raw_body import read_limited_body
from daengs_backend.schemas.cardimage import CardImageResponse, JudgeOut
from daengs_backend.services import ai_card_engine
from daengs_backend.services.ai_card_engine import default_engine, default_judge
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError
from daengs_cardimage.engine import EngineError
from daengs_cardimage.photo import MAX_PHOTO_BYTES, PhotoError

router = APIRouter(prefix="/admin/cardimage", tags=["admin-cardimage"])
# `require(...)` 는 부를 때마다 새 함수를 만든다 — 라우터와 테스트가 같은 dependency_overrides
# 키를 쓰려면 상수 하나로 고정해야 한다 (tests/test_admin_audit_view.py 와 같은 방식).
_INSPECT = require(Perm.SEARCH_INSPECT)


@router.post("/generate", response_model=CardImageResponse)
async def generate(
    request: Request,
    _admin: Annotated[Principal, Depends(_INSPECT)],
    dog_name: Annotated[str, Query(min_length=1, max_length=40)],
    month: Annotated[int, Query(ge=1, le=12)] = 4,
) -> CardImageResponse:
    if not dog_name.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"code": "bad_name", "message": "강아지 이름이 비어 있습니다"}
        )
    body = await read_limited_body(request, MAX_PHOTO_BYTES)
    # 헤더가 없으면 빈 문자열을 그대로 넘긴다 — `prepare_photo` 가 허용 MIME 밖으로 보고
    # `PhotoError("bad_mime")` 를 내면 아래에서 400 으로 바뀐다. `ALLOWED_MIME` 이 소문자
    # 집합이라(`image/jpeg` 등) `.lower()` 없이는 `Image/JPEG` 같은 값이 그냥 걸러진다.
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    started = time.monotonic()
    try:
        # Gemini SDK 는 동기 호출이라(20~60초) 이벤트 루프를 막지 않도록 스레드로 뺀다
        # (services/chat_summary.py 의 `_call` 과 같은 방식).
        card = await asyncio.to_thread(
            ai_card_engine.generate,
            photo=body,
            content_type=content_type,
            month=month,
            dog_name=dog_name,
            engine=default_engine(),
            judge=default_judge(),
        )
    except PhotoError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": exc.code, "message": exc.detail}) from None
    except MonthNotOpenError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "month_closed", "message": "열려 있지 않은 달입니다"}
        ) from None
    except CardImageUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "unavailable", "message": str(exc)}
        ) from None
    except EngineError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"code": exc.code, "message": exc.detail}) from None
    return CardImageResponse(
        month=card.month,
        title=card.title,
        attempts=card.attempts,
        judge=JudgeOut(**card.judge.__dict__) if card.judge else None,
        png_base64=base64.b64encode(card.png).decode("ascii"),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
