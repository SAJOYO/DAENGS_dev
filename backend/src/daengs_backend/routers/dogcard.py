"""`/app/cards/*` — 도감 카드 HTTP 경계 (D-052).

앱이 Room 과 `filesDir/cards/<id>.png` 에만 갖고 있던 카드를 서버로 올립니다.
그전까지는 **폰을 바꾸면 뽑은 카드가 전부 사라졌습니다.**

⚠️ **쓰기가 PUT 입니다.** 다른 도메인은 POST 로 열고 backend 가 id 를 만들지만,
   카드는 **앱이 만든 id 를 경로로 받습니다** — 오프라인에서 먼저 만들어지기
   때문입니다. 그래서 같은 카드를 여러 번 올려도 한 장입니다(멱등).

⚠️ **없는 것과 남의 것은 같은 404 입니다** — 403 을 주면 "그 카드가 존재한다" 가
   샙니다. 다만 **upsert 만 예외로 409** 입니다: 남이 가진 id 로 올린 것은 앱이
   "id 를 다시 만들어 재시도" 할 수 있어야 하는데, 404 면 그걸 구분 못 합니다.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.core.storage import StorageNotConfiguredError
from daengs_backend.models import DogCard
from daengs_backend.repositories import dogcard as card_repo
from daengs_backend.schemas.dogcard import (
    DogCardFaceTicket,
    DogCardListResponse,
    DogCardResponse,
    DogCardUpsert,
    DogCardUpsertResponse,
)
from daengs_backend.services import dogcard as card_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/app/cards", tags=["cards"])

Session = Annotated[AsyncSession, Depends(get_session)]

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "카드를 찾을 수 없습니다.")

#: 저장소가 안 켜졌을 때 **사용자에게** 보여 줄 문장.
#:
#: ⚠️ **예외 메시지를 그대로 내보내면 안 됩니다** — 운영자용이라 환경 변수 이름이
#:    들어 있습니다 (보행 라우터가 그것을 앱 화면에 띄운 적이 있습니다, 2026-09-03).
_STORAGE_NOT_READY = "카드 보관은 아직 준비 중이에요."


def _storage_unavailable(exc: StorageNotConfiguredError) -> HTTPException:
    log.warning("도감 카드 저장소가 준비되지 않았습니다: %s", exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _STORAGE_NOT_READY)


def _conflict(exc: card_service.DogCardConflictError) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, {"code": exc.code, "message": exc.detail}
    )


def _to_response(card: DogCard, face_url: str | None = None) -> DogCardResponse:
    return DogCardResponse(
        id=card.id,
        template_id=card.template_id,
        dog_id=card.dog_id,
        dog_name=card.dog_name,
        drawn_at=card.drawn_at,
        code_text=card.code_text,
        user_framed=card.user_framed,
        core_left=card.core_left,
        core_top=card.core_top,
        core_right=card.core_right,
        core_bottom=card.core_bottom,
        has_face=card.face_storage_key is not None,
        face_url=face_url,
    )


@router.put("/{card_id}", response_model=DogCardUpsertResponse)
async def upsert_card(
    card_id: uuid.UUID,
    body: DogCardUpsert,
    user: CurrentAppUser,
    session: Session,
    response: Response,
) -> DogCardUpsertResponse:
    """카드 한 장을 올립니다. **여러 번 보내도 한 장입니다.**

    새로 생기면 201, 이미 있으면 200 입니다. `face_upload` 는 **얼굴이 아직 없을
    때만** 옵니다 — 이미 올렸으면 앱이 더 할 일이 없습니다.
    """
    try:
        card, ticket, created = await card_service.upsert_card(
            session, user.app_user_id, card_id, body
        )
    except card_service.DogCardNotFoundError:
        # 남의 아이로 뽑았다고 적었습니다. 아이 쪽 404 와 같은 말로 뭉갭니다.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다."
        ) from None
    except card_service.DogCardConflictError as exc:
        raise _conflict(exc) from None
    except StorageNotConfiguredError as exc:
        raise _storage_unavailable(exc) from None

    if created:
        response.status_code = status.HTTP_201_CREATED
    return DogCardUpsertResponse(
        card=_to_response(card),
        face_upload=(
            DogCardFaceTicket(
                storage_key=ticket.storage_key,
                upload_url=ticket.upload_url,
                upload_headers=ticket.headers,
                expires_in_seconds=ticket.expires_in_seconds,
            )
            if ticket is not None
            else None
        ),
        created=created,
    )


@router.post("/{card_id}/face/confirm", response_model=DogCardResponse)
async def confirm_face(
    card_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> DogCardResponse:
    """올라온 얼굴 그림을 확정합니다."""
    try:
        card = await card_service.confirm_face(session, user.app_user_id, card_id)
    except card_service.DogCardNotFoundError:
        raise _NOT_FOUND from None
    except card_service.DogCardConflictError as exc:
        raise _conflict(exc) from None
    except StorageNotConfiguredError as exc:
        raise _storage_unavailable(exc) from None
    return _to_response(card)


@router.get("", response_model=DogCardListResponse)
async def list_cards(user: CurrentAppUser, session: Session) -> DogCardListResponse:
    """내 카드 전부, **최근에 뽑은 것부터.**

    **얼굴 주소를 안 싣습니다** — N 장마다 저장소를 두드리게 됩니다. 앱은 `has_face`
    로 무엇을 받아야 하는지 알고, 필요한 것만 단건 조회합니다.
    """
    cards = await card_service.list_cards(session, user.app_user_id)
    return DogCardListResponse(cards=[_to_response(c) for c in cards])


@router.get("/{card_id}", response_model=DogCardResponse)
async def get_card(
    card_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> DogCardResponse:
    """카드 하나와 얼굴 그림 주소."""
    try:
        card, url = await card_service.get_card(session, user.app_user_id, card_id)
    except card_service.DogCardNotFoundError:
        raise _NOT_FOUND from None
    except StorageNotConfiguredError as exc:
        raise _storage_unavailable(exc) from None
    return _to_response(card, url)


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(
    card_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> None:
    """카드 하나를 지웁니다. **얼굴 그림까지 지웁니다.**"""
    try:
        await card_service.delete_card(session, user.app_user_id, card_id)
    except card_service.DogCardNotFoundError:
        raise _NOT_FOUND from None
    except StorageNotConfiguredError as exc:
        raise _storage_unavailable(exc) from None


# ── local 저장소의 bridge ────────────────────────────────────────────────
#
# ⚠️ **인증 헤더를 요구하지 않습니다 — 대신 키가 자격입니다.** Signed URL 을 흉내 내는
#    자리라, 헤더를 요구하면 저장소를 GCS 로 되돌릴 때 앱 코드가 또 바뀝니다.
#    대신 **backend 가 아는 키인지**를 DB 로 확인합니다.


def _local_bridge():
    from daengs_backend.core.storage import LocalBridgeStorage, get_storage

    storage = get_storage()
    if not isinstance(storage, LocalBridgeStorage):
        raise _NOT_FOUND
    return storage


@router.put("/_bridge/upload/{storage_key:path}", include_in_schema=False)
async def _bridge_upload(session: Session, storage_key: str, request: Request) -> Response:
    """카드의 얼굴 그림을 받습니다. 디스크로 흘려 씁니다.

    ⚠️ **키에서 카드를 되짚습니다.** 다른 도메인은 DB 에 적힌 키로 찾지만, 카드는
       confirm 전까지 `face_storage_key` 가 비어 있습니다 (얼굴이 올라와야 채워집니다).
       그래서 키의 모양(`cards/<user>/<card>/face.png`)에서 card_id 를 꺼내 그 카드가
       **정말 있고, 그 사람 것이고, 아직 얼굴이 없는지**를 봅니다.
    """
    storage = _local_bridge()

    # cards/<app_user_id>/<card_id>/face.png
    parts = storage_key.split("/")
    if len(parts) != 4 or parts[0] != "cards" or parts[3] != "face.png":
        raise _NOT_FOUND
    try:
        owner_id = uuid.UUID(parts[1])
        card_id = uuid.UUID(parts[2])
    except ValueError:
        raise _NOT_FOUND from None

    card = await card_repo.get_owned(session, owner_id, card_id)
    if card is None or card.face_storage_key is not None:
        # 없는 카드 · 남의 카드 · 이미 확정된 얼굴 = 전부 없는 경로와 같은 404.
        raise _NOT_FOUND

    declared_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if declared_type != card_service.CARD_FACE_CONTENT_TYPE:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "얼굴 그림은 PNG 여야 합니다 (구멍에 끼우려면 알파가 필요합니다).",
        )

    limit = card_service.MAX_CARD_FACE_BYTES
    too_large = HTTPException(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        f"얼굴 그림은 {limit // (1024 * 1024)} MiB 이하여야 합니다.",
    )

    declared_size = request.headers.get("content-length")
    if declared_size is not None:
        try:
            if int(declared_size) > limit:
                raise too_large
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Content-Length 가 올바르지 않습니다."
            ) from None

    try:
        written = 0
        with storage.open_write(storage_key, exclusive=True) as stream:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise too_large
                stream.write(chunk)
            if written == 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "빈 그림은 업로드할 수 없습니다."
                )
    except FileExistsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이미 올린 얼굴 그림은 같은 티켓으로 덮어쓸 수 없습니다.",
        ) from None
    return Response(status_code=status.HTTP_200_OK)


@router.get("/_bridge/download/{storage_key:path}", include_in_schema=False)
async def _bridge_download(session: Session, storage_key: str):
    """확정된 얼굴 그림을 내려줍니다.

    여기는 **DB 에 적힌 키로** 찾습니다 — 확정된 것만 내려주면 되기 때문입니다.
    """
    from fastapi.responses import FileResponse

    storage = _local_bridge()
    if await card_repo.find_by_face_key(session, storage_key) is None:
        raise _NOT_FOUND
    path = storage.local_path(storage_key)
    if not path.exists():
        raise _NOT_FOUND
    return FileResponse(path, media_type=card_service.CARD_FACE_CONTENT_TYPE)
