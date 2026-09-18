"""`/app/ai-cards/*` — 앱 사용자 AI 도감 카드 HTTP 경계 (#537, D-076).

**비동기입니다.** POST 는 202 와 `status: generating` 을 바로 주고, 앱은 `GET /{id}` 로 다시
조회합니다. 사진은 요청 본문 원시 바이트, 메타는 쿼리입니다 (multipart 없음).

**만들 카드를 고르는 쿼리가 둘입니다 — 전환기입니다** (#593, D-085, 사용자 결정 A):
옛 앱의 `month=4`(달 1~12)와 새 앱의 `card=strawberry`·`card=4`. 규칙은 `_selector`,
응답은 둘을 함께 싣습니다(`month` 는 종류 카드에서 `null`, `card` 는 늘 채워집니다).

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
from daengs_backend.services.ai_card_quota import (
    AiCardBusyError,
    AiCardLimitError,
    AiCardTakenError,
)
from daengs_cardimage import CardImageUnavailable, catalog
from daengs_cardimage.catalog import PHOTO_GUIDANCE, MonthNotOpenError
from daengs_cardimage.photo import MAX_PHOTO_BYTES, PhotoError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/app/ai-cards", tags=["ai-cards"])

Session = Annotated[AsyncSession, Depends(get_session)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


def _not_found() -> HTTPException:
    return _error(status.HTTP_404_NOT_FOUND, "not_found", "카드를 찾을 수 없습니다.")


def _selector(month: int | None, card: str | None) -> catalog.CardSelector:
    """`month`(옛 앱)와 `card`(새 앱)를 **하나의 선택자**로 (#593, D-085 — 사용자 결정 A, 전환기).

    - `month` 만 → 그 달. **옛 앱의 요청이 지금과 글자 하나까지 같게 도는 길입니다.**
    - `card` 만 → 숫자면 달 정수, 아니면 종류 문자열(`catalog.KINDS`).
      `routers/admin_cardimage.py::_selector` 와 같은 규칙입니다 — 쿼리는 늘 문자열이라
      여기서 갈라야 `"4"` 가 종류로 오해받지 않습니다.
    - **둘 다** → 같은 카드를 가리킬 때만 통과합니다. 어긋나면 400 `card_conflict` 입니다 —
      한쪽을 조용히 이기게 하면 사용자가 고른 것과 다른 카드가 나오고, 어느 쪽이 이기는지는
      앱과 서버가 서로 다르게 기억하게 됩니다.
    - **둘 다 없음** → 400 `card_required`. 옛 앱은 `month` 를 늘 보내므로 여기 안 옵니다.
    """
    chosen: catalog.CardSelector | None = None
    if card is not None:
        value = card.strip()
        chosen = int(value) if value.isdigit() else value
    if month is not None:
        if chosen is not None and chosen != month:
            raise _error(
                status.HTTP_400_BAD_REQUEST, "card_conflict", "고른 카드가 서로 달라요. 다시 시도해 주세요."
            )
        chosen = month
    if chosen is None:
        raise _error(status.HTTP_400_BAD_REQUEST, "card_required", "만들 카드를 고르지 않았어요.")
    return chosen


def _closed(selector: catalog.CardSelector) -> HTTPException:
    """없거나 닫힌 카드의 404.

    **코드는 파라미터 이름이 아니라 고른 카드를 따릅니다** — 달이면 `month_closed`(옛 앱이
    그것으로 갈라 쓰고 있을 수 있고, 앱 소스는 이 저장소에 없습니다), 종류면 `card_closed`.
    옛 앱은 달만 고를 수 있으므로 `month_closed` 밖에 못 봅니다.
    """
    if isinstance(selector, int):
        return _error(status.HTTP_404_NOT_FOUND, "month_closed", "지금은 만들 수 없는 달이에요.")
    return _error(status.HTTP_404_NOT_FOUND, "card_closed", "지금은 만들 수 없는 카드예요.")


def _with_topic(name: str) -> str:
    """이름 뒤에 은/는. 마지막 글자가 한글 음절이 아니면(영문·숫자) 받침을 모르므로 `은(는)`."""
    last = name[-1]
    if "가" <= last <= "힣":
        return name + ("은" if (ord(last) - ord("가")) % 28 else "는")
    return name + "은(는)"


def _to_response(
    card: AiCard,
    image_url: str | None = None,
    *,
    done: int | None = None,
    total: int | None = None,
    finished: bool | None = None,
) -> AiCardResponse:
    return AiCardResponse(
        id=card.id,
        dog_id=card.dog_id,
        month=card.month,
        card=card.card_key,
        dog_name=card.dog_name,
        title=card.title,
        status=card.status,
        error_code=card.error_code,
        likeness=card.likeness,
        attempts=card.attempts,
        width=card.width,
        height=card.height,
        created_at=card.created_at,
        pick_group=card.pick_group,
        done=done,
        total=total,
        finished=finished,
        image_url=image_url,
    )


@router.post("", response_model=AiCardResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_card(
    request: Request,
    user: CurrentAppMemberTokenOnly,
    session: Session,
    dog_name: Annotated[str, Query(min_length=1, max_length=40)],
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    card: Annotated[str | None, Query(min_length=1, max_length=20)] = None,
    dog_id: uuid.UUID | None = None,
    title_name: Annotated[str | None, Query(max_length=40)] = None,
) -> AiCardResponse:
    """사진 한 장으로 카드 만들기를 **시작합니다.** 끝나면 `GET /{id}` 가 `ready` 를 줍니다.

    **만들 카드는 `month` 나 `card` 로 고릅니다 — 전환기라 둘 다 받습니다** (#593, D-085,
    사용자 결정 A). `month=4` 만 보내는 옛 앱은 지금과 똑같이 돕니다. 새 앱은
    `card=strawberry`(종류) 나 `card=4`(달)를 보냅니다. 규칙은 `_selector` 에 있습니다.

    **종류 카드에는 열림/닫힘 설정이 없습니다** — 달은 `DAENGS_CARDIMAGE_MONTHS` 로 잠그지만
    종류는 **카탈로그(`daengs_cardimage.catalog._KIND_CARDS`)에 있으면 열린 것**입니다
    (#593 에서 정함). 달의 잠금은 틀 12장을 한꺼번에 넣어 두고 검증된 것만 여는 장치였는데,
    종류는 검증을 마친 것만 한 장씩 카탈로그에 넣으므로 「넣는 행위」가 곧 「여는 행위」입니다.
    설정을 하나 더 두면 카탈로그와 늘 같은 값을 갖는 목록이 둘이 됩니다. 나중에 종류를
    미리 넣어 두고 나중에 열어야 할 일이 생기면 그때 `DAENGS_CARDIMAGE_KINDS` 를 만드세요.

    `title_name` 은 제목에만 씁니다 — 비우면 `dog_name` (#543).

    인증은 **토큰만** 봅니다 (`CurrentAppMemberTokenOnly`) — `CurrentAppUser` 는 요청 세션에서
    `app_users FOR UPDATE` 를 먼저 잡아, 최대 20MB 본문을 받는 동안 잠금과 연결을 쥐게 됩니다.
    active 확인은 본문을 다 받고 사진을 준비한 뒤 서비스가 같은 잠금으로 합니다.
    """
    if not dog_name.strip():
        raise _error(status.HTTP_400_BAD_REQUEST, "bad_name", "강아지 이름이 비어 있습니다.")
    # 본문(최대 20MB)을 받기 **전에** 고른 카드부터 봅니다 — 어차피 거절할 요청입니다.
    selector = _selector(month, card)
    body = await read_limited_body(request, MAX_PHOTO_BYTES)
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    try:
        made = await ai_card_service.start(
            session,
            user.app_user_id,
            photo=body,
            content_type=content_type,
            card=selector,
            dog_name=dog_name,
            dog_id=dog_id,
            title_name=title_name,
        )
    except PhotoError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, exc.code, exc.detail) from None
    except ai_card_service.AiCardUserNotActiveError:
        # `current_app_user` 와 같은 문장 — 앱이 재로그인으로 알아듣는 자리입니다.
        raise _error(status.HTTP_401_UNAUTHORIZED, "not_active", "다시 로그인해 주세요.") from None
    except MonthNotOpenError:
        raise _closed(selector) from None
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
    except AiCardTakenError:
        name = " ".join(dog_name.split())
        # 코드도 문장도 **고른 카드**를 따릅니다 — 달은 `month_taken` · "4월 카드"(옛 앱이 보던 그대로),
        # 종류는 `card_taken` · "딸기 카드". 라벨을 여기서 짓지 않는 이유는 콘솔이 같은 글자를
        # 써야 해서입니다 (`daengs_cardimage.catalog.KIND_LABELS`).
        code = "month_taken" if isinstance(selector, int) else "card_taken"
        raise _error(
            status.HTTP_409_CONFLICT,
            code,
            f"{_with_topic(name)} 이미 {catalog.label(selector)} 카드가 있어요.",
        ) from None
    except AiCardLimitError:
        raise _error(
            status.HTTP_429_TOO_MANY_REQUESTS, "limit_reached", "오늘은 카드를 더 만들 수 없어요. 내일 다시 시도해 주세요."
        ) from None
    done, total, finished = await ai_card_service.group_progress(session, user.app_user_id, made)
    return _to_response(made, done=done, total=total, finished=finished)


@router.get("", response_model=AiCardListResponse)
async def list_cards(user: CurrentAppUser, session: Session) -> AiCardListResponse:
    """내 카드 전부, 최근 것부터 + 오늘 남은 횟수. **이미지 주소는 안 싣습니다** — 필요한 것만 단건 조회합니다."""
    cards = await ai_card_service.list_cards(session, user.app_user_id)
    daily_limit, daily_remaining = await ai_card_service.daily_status(session, user.app_user_id)
    return AiCardListResponse(
        cards=[_to_response(c) for c in cards],
        daily_limit=daily_limit,
        daily_remaining=daily_remaining,
        photo_guidance=PHOTO_GUIDANCE,
    )


@router.get("/{card_id}", response_model=AiCardResponse)
async def get_card(card_id: uuid.UUID, user: CurrentAppUser, session: Session) -> AiCardResponse:
    try:
        card, url = await ai_card_service.get_card(session, user.app_user_id, card_id)
    except ai_card_service.AiCardNotFoundError:
        raise _not_found() from None
    except StorageNotConfiguredError as exc:
        log.warning("AI 카드 저장소가 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage", "카드 보관은 아직 준비 중이에요.") from None
    done, total, finished = await ai_card_service.group_progress(session, user.app_user_id, card)
    return _to_response(card, url, done=done, total=total, finished=finished)


@router.post("/{card_id}/choose", response_model=AiCardResponse)
async def choose_card(card_id: uuid.UUID, user: CurrentAppUser, session: Session) -> AiCardResponse:
    """고른 카드만 남기고, 같은 요청에서 나온 형제 카드를 지웁니다 (#572 Task 4)."""
    try:
        card, url = await ai_card_service.choose_card(session, user.app_user_id, card_id)
    except ai_card_service.AiCardNotFoundError:
        raise _not_found() from None
    except ai_card_service.AiCardNotReadyError:
        raise _error(
            status.HTTP_409_CONFLICT, "not_ready", "아직 만들어지는 중이거나 실패한 카드는 고를 수 없어요."
        ) from None
    except StorageNotConfiguredError as exc:
        log.warning("AI 카드 저장소가 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage", "카드 보관은 아직 준비 중이에요.") from None
    done, total, finished = await ai_card_service.group_progress(session, user.app_user_id, card)
    return _to_response(card, url, done=done, total=total, finished=finished)


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
