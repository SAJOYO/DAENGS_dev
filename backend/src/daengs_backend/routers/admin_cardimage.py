"""`/admin/cardimage/*` — 콘솔의 「도감 카드 생성」 탭이 부르는 점검 경로 (#496, #592).

카드(달 1~12 + 딸기·상추)와 엔진(Nano Banana 2 · FLUX.2-klein-4B)을 골라 한 장 뽑고, 그
결과를 **콘솔 전용 표**(`admin_ai_cards`)에 남겨 다시 보고 지운다. 사진 원본은 저장하지
않는다 — 남는 것은 만들어진 카드 PNG 뿐이다.

앱 경로(`/app/ai-cards`)와 **아무것도 공유하지 않는다** — 하루 한도도, 동시 생성 세마포어도,
큐도 없다. 생성은 동기라 라우터가 그 자리에서 20~60초를 기다린다 (nginx 300s).

사진은 요청 본문 원시 바이트다 (이 저장소는 multipart 를 쓰지 않는다 — bridge 업로드와 같은 방식).

⚠️ **저장 실패는 요청을 죽이지 않는다** — 이미 돈이 나간 카드를 버리게 된다. PNG 는 그대로
   응답하고 `stored=false` 와 경고 로그만 남긴다 (spec ④).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Perm, Principal, require
from daengs_backend.models import AdminAiCard
from daengs_backend.routers.raw_body import read_limited_body
from daengs_backend.schemas.cardimage import (
    AdminCardListResponse,
    AdminCardOut,
    CardImageOptions,
    CardImageResponse,
    CardOption,
    EngineOption,
    JudgeOut,
)
from daengs_backend.services import admin_card_store, ai_card_engine
from daengs_backend.services.ai_card_engine import default_judge, engine_by_name, gpu_path_active
from daengs_cardimage import CardImageUnavailable, catalog
from daengs_cardimage.catalog import PHOTO_GUIDANCE, MonthNotOpenError
from daengs_cardimage.engine import EngineError
from daengs_cardimage.photo import MAX_PHOTO_BYTES, PhotoError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/cardimage", tags=["admin-cardimage"])
# `require(...)` 는 부를 때마다 새 함수를 만든다 — 라우터와 테스트가 같은 dependency_overrides
# 키를 쓰려면 상수 하나로 고정해야 한다 (tests/test_admin_audit_view.py 와 같은 방식).
_INSPECT = require(Perm.SEARCH_INSPECT)

Session = Annotated[AsyncSession, Depends(get_session)]

EngineName = Literal["gemini", "cardgen"]

#: 엔진 이름 → 화면에 띄우는 모델 이름. 줄임말을 쓰지 않는다 (문서 표기 규칙).
_ENGINE_LABELS: dict[str, str] = {"gemini": "Nano Banana 2", "cardgen": "FLUX.2-klein-4B"}

#: 달이 아닌 카드의 한국어 이름. `catalog` 는 프롬프트용 영어만 갖고 있어서 여기서 붙인다.
_KIND_LABELS: dict[str, str] = {"strawberry": "딸기", "lettuce": "상추"}

#: 목록 기본·최대 건수. 미리보기를 한 장씩 따로 받는 화면이라 넉넉히 줄 이유가 없다.
_LIST_DEFAULT = 50
_LIST_MAX = 200


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


def _selector(card: str) -> catalog.CardSelector:
    """쿼리 문자열을 `CardSelector` 로. **숫자면 달 정수**, 아니면 종류 문자열이다.

    쿼리는 늘 문자열이라 여기서 갈라야 한다 — `"4"` 를 그대로 넘기면 `_KIND_CARDS` 를 뒤져
    없는 카드가 된다. 없는 카드는 `catalog.resolve` 가 `MonthNotOpenError` 로 알려 준다.
    """
    value = card.strip()
    return int(value) if value.isdigit() else value


def _to_out(card: AdminAiCard) -> AdminCardOut:
    return AdminCardOut(
        id=card.id,
        admin_user_id=card.admin_user_id,
        card=card.card_key,
        dog_name=card.dog_name,
        title=card.title,
        engine=card.engine,
        seed=card.seed,
        attempts=card.attempts,
        likeness=card.likeness,
        judge_note=card.judge_note,
        width=card.width,
        height=card.height,
        size_bytes=card.size_bytes,
        elapsed_ms=card.elapsed_ms,
        created_at=card.created_at,
    )


@router.get("/options", response_model=CardImageOptions)
async def options(_admin: Annotated[Principal, Depends(_INSPECT)]) -> CardImageOptions:
    """화면이 그릴 카드 14장·엔진 둘·사진 안내. **프론트에 복제하지 않기 위한 경로다** (#592).

    달은 `DAENGS_CARDIMAGE_MONTHS` 로 거르지 않는다 — 그 잠금은 앱 사용자용이고, 콘솔은 닫힌
    달의 틀을 확인하는 자리다. 닫힌 달을 고르면 `POST /generate` 가 404 로 말해 준다.
    """
    cards = [CardOption(key=str(m), label=f"{m}월 · {catalog.get(m).card_name}") for m in range(1, 13)]
    cards += [
        CardOption(key=kind, label=f"{_KIND_LABELS.get(kind, kind)} · {catalog.resolve(kind).card_name}")
        for kind in catalog.KINDS
    ]

    gemini_ready = bool(settings.cardimage_gemini_api_key.get_secret_value().strip())
    gpu_ready = gpu_path_active()
    engines = [
        EngineOption(
            key="gemini",
            label=_ENGINE_LABELS["gemini"],
            available=gemini_ready,
            reason=None if gemini_ready else "DAENGS_CARDIMAGE_GEMINI_API_KEY 가 비어 있습니다",
        ),
        EngineOption(
            key="cardgen",
            label=_ENGINE_LABELS["cardgen"],
            available=gpu_ready,
            reason=None if gpu_ready else "DAENGS_CARDGEN_URL 이 비어 있어 GPU 경로가 꺼져 있습니다",
        ),
    ]
    return CardImageOptions(cards=cards, engines=engines, photo_guidance=PHOTO_GUIDANCE)


@router.post("/generate", response_model=CardImageResponse)
async def generate(
    request: Request,
    admin: Annotated[Principal, Depends(_INSPECT)],
    session: Session,
    dog_name: Annotated[str, Query(min_length=1, max_length=40)],
    card: Annotated[str, Query(min_length=1, max_length=20)] = "4",
    engine: EngineName = "gemini",
    seed: Annotated[int | None, Query(ge=0)] = None,
) -> CardImageResponse:
    """카드 한 장을 뽑아 저장한다. 사진은 본문 원시 바이트.

    본문(최대 20MB)을 읽기 **전에** 고른 값부터 본다 — 어차피 거절할 요청에 업로드를 다 받을
    이유가 없다.
    """
    if not dog_name.strip():
        raise _error(status.HTTP_400_BAD_REQUEST, "bad_name", "강아지 이름이 비어 있습니다")
    selector = _selector(card)
    try:
        catalog.resolve(selector)
    except MonthNotOpenError:
        raise _error(status.HTTP_404_NOT_FOUND, "card_closed", "없는 카드입니다") from None
    if engine == "cardgen" and not gpu_path_active():
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "cardgen_disabled", "GPU 엔진이 켜져 있지 않습니다"
        )
    if engine != "cardgen" and seed is not None:
        # Nano Banana 2 는 seed 를 버린다 — 받아 두고 무시하면 "그 seed 로 다시 뽑을 수 있다"는
        # 거짓말을 응답에 싣게 된다 (`services/ai_card_engine.plan_request_seeds` 와 같은 이유).
        raise _error(
            status.HTTP_400_BAD_REQUEST, "seed_not_supported", "이 엔진은 seed 를 쓰지 않습니다"
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
        made = await asyncio.to_thread(
            ai_card_engine.generate,
            photo=body,
            content_type=content_type,
            card=selector,
            dog_name=dog_name,
            engine=engine_by_name(engine),
            judge=default_judge(),
            seed=seed,
        )
    except PhotoError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, exc.code, exc.detail) from None
    except MonthNotOpenError:
        # `selector` 로 이미 걸렀지만 달 잠금(`DAENGS_CARDIMAGE_MONTHS`)은 생성 안에서 본다.
        raise _error(status.HTTP_404_NOT_FOUND, "card_closed", "열려 있지 않은 달입니다") from None
    except CardImageUnavailable as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable", str(exc)) from None
    except EngineError as exc:
        raise _error(status.HTTP_502_BAD_GATEWAY, exc.code, exc.detail) from None
    elapsed_ms = int((time.monotonic() - started) * 1000)

    # GPU 경로에서만 seed 를 기록한다 — Nano Banana 2 는 받은 seed 를 버리므로, 적어 두면
    # 나중에 "이 값으로 다시 뽑을 수 있다"고 읽힌다 (`services/ai_card.py::_finish_ready` 와 같은 규칙).
    used_seed = made.seed if engine == "cardgen" else None
    row: AdminAiCard | None = None
    try:
        row = await admin_card_store.save(
            session,
            admin_user_id=admin.admin_id,
            card_key=made.card_key,
            dog_name=dog_name,
            title=made.title,
            engine=engine,
            seed=used_seed,
            attempts=made.attempts,
            judge=made.judge,
            png=made.png,
            elapsed_ms=elapsed_ms,
        )
    # 저장이 무엇으로 실패하든(DB 가 없든, 표가 아직 없든) 뽑은 카드는 돌려준다 (spec ④).
    except Exception:
        log.warning("콘솔 카드를 저장하지 못했습니다 (card=%s, engine=%s)", made.card_key, engine, exc_info=True)
    if row is None:
        log.warning("콘솔 카드가 저장되지 않았습니다 — 응답으로만 나갑니다 (card=%s)", made.card_key)

    return CardImageResponse(
        id=row.id if row is not None else None,
        card=made.card_key,
        title=made.title,
        attempts=made.attempts,
        judge=JudgeOut(**made.judge.__dict__) if made.judge else None,
        png_base64=base64.b64encode(made.png).decode("ascii"),
        elapsed_ms=elapsed_ms,
        engine=engine,
        seed=used_seed,
        stored=row is not None,
    )


@router.get("/cards", response_model=AdminCardListResponse)
async def list_cards(
    _admin: Annotated[Principal, Depends(_INSPECT)],
    session: Session,
    limit: Annotated[int, Query(ge=1, le=_LIST_MAX)] = _LIST_DEFAULT,
) -> AdminCardListResponse:
    """최근 것부터, **관리자 전원의 카드**를 준다 (사용자 결정 09-18)."""
    cards = await admin_card_store.recent(session, limit=limit)
    return AdminCardListResponse(cards=[_to_out(c) for c in cards])


@router.get("/cards/{card_id}/image")
async def card_image(
    card_id: uuid.UUID,
    _admin: Annotated[Principal, Depends(_INSPECT)],
    session: Session,
) -> Response:
    """PNG 바이트. `<img>` 는 Bearer 헤더를 못 싣으므로 콘솔이 blob 으로 받아 그린다 (spec ④) —
    앱의 무인증 bridge 를 여기로 끌어오지 않는다."""
    got = await admin_card_store.load_png(session, card_id)
    if got is None:
        raise _error(status.HTTP_404_NOT_FOUND, "not_found", "카드를 찾을 수 없습니다")
    return Response(content=got[1], media_type=admin_card_store.ADMIN_AI_CARD_CONTENT_TYPE)


@router.delete("/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(
    card_id: uuid.UUID,
    _admin: Annotated[Principal, Depends(_INSPECT)],
    session: Session,
) -> None:
    """행과 저장된 PNG 를 지운다. 콘솔 권한이면 남의 카드도 지울 수 있다 (사용자 결정 09-18)."""
    if not await admin_card_store.remove(session, card_id):
        raise _error(status.HTTP_404_NOT_FOUND, "not_found", "카드를 찾을 수 없습니다")
