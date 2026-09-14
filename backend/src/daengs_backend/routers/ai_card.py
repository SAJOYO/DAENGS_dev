"""`/app/ai-cards/*` — 앱 사용자 AI 도감 카드 HTTP 경계 (#537, D-076).

**비동기입니다.** POST 는 202 와 `status: generating` 을 바로 주고, 앱은 `GET /{id}` 로 다시
조회합니다. 사진은 요청 본문 원시 바이트, 메타는 쿼리입니다 (multipart 없음).

⚠️ **오류 본문의 `message` 는 앱이 그대로 띄웁니다.** 예외 메시지를 그대로 내보내지 마세요 —
   운영자용이라 환경 변수 이름이 들어 있습니다 (보행 라우터가 그것을 앱 화면에 띄운 적이 있습니다).
⚠️ **없는 것과 남의 것은 같은 404 입니다** — 403 이면 "그 id 는 존재한다" 가 샙니다.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppMemberTokenOnly, CurrentAppUser
from daengs_backend.core.storage import StorageNotConfiguredError
from daengs_backend.models import AiCard
from daengs_backend.repositories import ai_card as ai_card_repo
from daengs_backend.routers.raw_body import read_limited_body
from daengs_backend.schemas.ai_card import AiCardListResponse, AiCardResponse
from daengs_backend.services import ai_card as ai_card_service
from daengs_backend.services.ai_card_quota import AiCardBusyError, AiCardLimitError
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError
from daengs_cardimage.photo import MAX_PHOTO_BYTES, PhotoError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/app/ai-cards", tags=["ai-cards"])

Session = Annotated[AsyncSession, Depends(get_session)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


def _not_found() -> HTTPException:
    return _error(status.HTTP_404_NOT_FOUND, "not_found", "카드를 찾을 수 없습니다.")


def _to_response(card: AiCard, image_url: str | None = None) -> AiCardResponse:
    return AiCardResponse(
        id=card.id,
        dog_id=card.dog_id,
        month=card.month,
        dog_name=card.dog_name,
        title=card.title,
        status=card.status,
        error_code=card.error_code,
        likeness=card.likeness,
        attempts=card.attempts,
        width=card.width,
        height=card.height,
        created_at=card.created_at,
        image_url=image_url,
    )


@router.post("", response_model=AiCardResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_card(
    request: Request,
    user: CurrentAppMemberTokenOnly,
    session: Session,
    month: Annotated[int, Query(ge=1, le=12)],
    dog_name: Annotated[str, Query(min_length=1, max_length=40)],
    dog_id: uuid.UUID | None = None,
) -> AiCardResponse:
    """사진 한 장으로 카드 만들기를 **시작합니다.** 끝나면 `GET /{id}` 가 `ready` 를 줍니다.

    인증은 **토큰만** 봅니다 (`CurrentAppMemberTokenOnly`) — `CurrentAppUser` 는 요청 세션에서
    `app_users FOR UPDATE` 를 먼저 잡아, 최대 20MB 본문을 받는 동안 잠금과 연결을 쥐게 됩니다.
    active 확인은 본문을 다 받고 사진을 준비한 뒤 서비스가 같은 잠금으로 합니다.
    """
    if not dog_name.strip():
        raise _error(status.HTTP_400_BAD_REQUEST, "bad_name", "강아지 이름이 비어 있습니다.")
    body = await read_limited_body(request, MAX_PHOTO_BYTES)
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    try:
        card = await ai_card_service.start(
            session,
            user.app_user_id,
            photo=body,
            content_type=content_type,
            month=month,
            dog_name=dog_name,
            dog_id=dog_id,
        )
    except PhotoError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, exc.code, exc.detail) from None
    except ai_card_service.AiCardUserNotActiveError:
        # `current_app_user` 와 같은 문장 — 앱이 재로그인으로 알아듣는 자리입니다.
        raise _error(status.HTTP_401_UNAUTHORIZED, "not_active", "다시 로그인해 주세요.") from None
    except MonthNotOpenError:
        raise _error(status.HTTP_404_NOT_FOUND, "month_closed", "지금은 만들 수 없는 달이에요.") from None
    except ai_card_service.AiCardNotFoundError:
        raise _error(status.HTTP_404_NOT_FOUND, "dog_not_found", "강아지를 찾을 수 없습니다.") from None
    except CardImageUnavailable as exc:
        log.warning("AI 카드 생성이 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable", "카드 만들기는 지금 준비 중이에요.") from None
    except StorageNotConfiguredError as exc:
        log.warning("AI 카드 저장소가 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage", "카드 보관은 아직 준비 중이에요.") from None
    except AiCardBusyError:
        raise _error(
            status.HTTP_409_CONFLICT, "already_generating", "만들고 있는 카드가 있어요. 끝나면 다시 시도해 주세요."
        ) from None
    except AiCardLimitError:
        raise _error(
            status.HTTP_429_TOO_MANY_REQUESTS, "limit_reached", "오늘은 카드를 더 만들 수 없어요. 내일 다시 시도해 주세요."
        ) from None
    return _to_response(card)


@router.get("", response_model=AiCardListResponse)
async def list_cards(user: CurrentAppUser, session: Session) -> AiCardListResponse:
    """내 카드 전부, 최근 것부터. **이미지 주소는 안 싣습니다** — 필요한 것만 단건 조회합니다."""
    cards = await ai_card_service.list_cards(session, user.app_user_id)
    return AiCardListResponse(cards=[_to_response(c) for c in cards])


@router.get("/{card_id}", response_model=AiCardResponse)
async def get_card(card_id: uuid.UUID, user: CurrentAppUser, session: Session) -> AiCardResponse:
    try:
        card, url = await ai_card_service.get_card(session, user.app_user_id, card_id)
    except ai_card_service.AiCardNotFoundError:
        raise _not_found() from None
    except StorageNotConfiguredError as exc:
        log.warning("AI 카드 저장소가 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage", "카드 보관은 아직 준비 중이에요.") from None
    return _to_response(card, url)


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(card_id: uuid.UUID, user: CurrentAppUser, session: Session) -> None:
    try:
        await ai_card_service.delete_card(session, user.app_user_id, card_id)
    except ai_card_service.AiCardNotFoundError:
        raise _not_found() from None
    except StorageNotConfiguredError as exc:
        log.warning("AI 카드 저장소가 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage", "카드 보관은 아직 준비 중이에요.") from None


# ── local 저장소의 bridge ────────────────────────────────────────────────
#
# ⚠️ **인증 헤더를 요구하지 않습니다 — 대신 키가 자격입니다** (`routers/dogcard.py` 와 같은 이유).
#    `ready` 행에 적힌 키만 내려줍니다.


@router.get("/_bridge/download/{storage_key:path}", include_in_schema=False)
async def _bridge_download(session: Session, storage_key: str):
    from fastapi.responses import FileResponse

    from daengs_backend.core.storage import LocalBridgeStorage

    storage = ai_card_service.get_storage()
    if not isinstance(storage, LocalBridgeStorage):
        raise _not_found()
    if await ai_card_repo.find_ready_by_storage_key(session, storage_key) is None:
        raise _not_found()
    path = storage.local_path(storage_key)
    if not path.exists():
        raise _not_found()
    return FileResponse(path, media_type=ai_card_service.AI_CARD_CONTENT_TYPE)
