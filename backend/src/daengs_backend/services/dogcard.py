"""도감 카드의 규칙. 트랜잭션 경계도 여기입니다 (`/app/cards/*`, D-052).

앱이 Room 과 `filesDir/cards/<id>.png` 에만 갖고 있던 것을 서버로 올립니다. 그전까지는
**폰을 바꾸면 뽑은 카드가 전부 사라졌습니다.**

⚠️ **다른 도메인과 쓰기 방향이 다릅니다.** 프로필·피부는 backend 가 id 와 키를 만들고
   앱이 그것을 받아 씁니다(원칙 6). 카드는 **앱이 만든 id 를 서버가 받습니다** —
   카드가 오프라인에서 먼저 만들어지기 때문입니다(로그인 없이 둘러보기로도 뽑습니다).
   앱이 그렇게 설계해 뒀습니다: "서버가 붙어도 이 id 를 그대로 올려서 **재전송이
   멱등해진다**".

   원칙 6 이 막으려던 것(남의 경로를 덮어쓰기)은 여기서 **소유자 검사**가 막습니다 —
   남이 이미 가진 id 로 올리면 409 이고, 저장소 키는 그 사람의 user_id 로 만들어집니다.

⚠️ **카드는 뽑힌 뒤로 안 바뀌는 물건입니다.** 그래서 upsert 는 "고치기" 가 아니라
   "같은 것을 한 번 더 보내기" 입니다. 확률표를 고쳤다고 이미 가진 카드가 바뀌면
   안 됩니다.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.storage import UploadTicket, build_card_face_key, get_storage
from daengs_backend.models import DogCard
from daengs_backend.repositories import dogcard as card_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.schemas.dogcard import DogCardUpsert

log = logging.getLogger(__name__)

#: 얼굴 그림 한 장의 상한(바이트).
#:
#: 구멍에 끼울 얼굴 하나라 크지 않습니다. PNG 는 알파 때문에 JPEG 보다 무겁지만
#: 그래도 이 선을 넘을 이유가 없습니다. 이 경로에는 nginx 전용 블록이 없어서
#: server 기본값 20m 아래에 있고, 그보다 낮아야 앱이 우리 413 을 받습니다.
MAX_CARD_FACE_BYTES = 4 * 1024 * 1024

#: 얼굴은 **PNG 뿐입니다** — 구멍에 끼우려면 알파가 필요해서 JPEG 은 못 씁니다.
CARD_FACE_CONTENT_TYPE = "image/png"

#: 카드 bridge 의 경로. 도메인마다 다릅니다.
CARD_BRIDGE_UPLOAD_PATH = "/app/cards/_bridge/upload"
CARD_BRIDGE_DOWNLOAD_PATH = "/app/cards/_bridge/download"


class DogCardNotFoundError(Exception):
    """내 카드가 아니거나 없습니다.

    **남의 것일 때도 이 예외입니다** — 403 으로 나누면 "그 id 는 존재한다" 가 샙니다.
    """


class DogCardConflictError(Exception):
    """카드 상태가 요청과 안 맞습니다. 라우터가 409 로 바꿉니다."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _face_ticket(card: DogCard) -> UploadTicket:
    key = build_card_face_key(card.app_user_id, card.id)
    return get_storage().create_upload_ticket(
        object_key=key,
        content_type=CARD_FACE_CONTENT_TYPE,
        bridge_upload_path=CARD_BRIDGE_UPLOAD_PATH,
        # 같은 티켓으로 두 번 못 올립니다. 카드는 안 바뀌는 물건이라 얼굴도 한 번뿐입니다.
        create_only=True,
    )


async def upsert_card(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    card_id: uuid.UUID,
    body: DogCardUpsert,
) -> tuple[DogCard, UploadTicket | None, bool]:
    """카드 한 장을 올립니다. **여러 번 보내도 한 장입니다.**

    돌려주는 것: (카드, 얼굴 티켓 또는 None, 새로 생겼는가).
    티켓은 **얼굴이 아직 없을 때만** 옵니다 — 이미 올렸으면 앱이 더 할 일이 없습니다.
    """
    # 남의 아이로 뽑았다고 적을 수 없습니다. FK 는 "존재하는 pets 행" 까지만 보장하고
    # 그게 내 것인지는 안 봅니다.
    if (
        body.dog_id is not None
        and await pet_repo.get_owned(session, app_user_id, body.dog_id) is None
    ):
        raise DogCardNotFoundError

    existing = await card_repo.get_any(session, card_id, for_update=True)
    if existing is not None:
        if existing.app_user_id != app_user_id:
            # ⚠️ 앱이 id 를 만드는 구조라 **여기가 원칙 6 을 대신합니다.**
            #    남이 가진 id 로 올리면 그 사람 카드를 덮어쓰게 됩니다.
            raise DogCardConflictError(
                "card_belongs_to_someone_else", "이미 다른 계정이 가진 카드입니다."
            )
        # **덮어쓰지 않습니다.** 카드는 뽑힌 뒤로 안 바뀌는 물건이라, 다시 올리는 것은
        # "같은 것을 한 번 더 보내기" 입니다. 여기서 값을 갈아끼우면 앱의 버그 하나가
        # 이미 뽑아 둔 카드를 조용히 바꿉니다.
        ticket = None if existing.face_storage_key else _face_ticket(existing)
        return existing, ticket, False

    card = DogCard(
        id=card_id,
        app_user_id=app_user_id,
        template_id=body.template_id,
        dog_id=body.dog_id,
        dog_name=body.dog_name,
        drawn_at=body.drawn_at,
        code_text=body.code_text,
        user_framed=body.user_framed,
        core_left=body.core_left,
        core_top=body.core_top,
        core_right=body.core_right,
        core_bottom=body.core_bottom,
    )
    card_repo.add(session, card)
    await session.commit()

    # 티켓은 **행을 만든 뒤에** 발급합니다. 먼저 주면 업로드는 됐는데 그 키가 무엇인지
    # 아무도 모르는 파일이 볼륨에 남습니다 — 저장소에는 FK 가 없습니다.
    return card, _face_ticket(card), True


async def confirm_face(
    session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID
) -> DogCard:
    """올라온 얼굴 그림을 확정합니다."""
    card = await card_repo.get_owned(session, app_user_id, card_id, for_update=True)
    if card is None:
        raise DogCardNotFoundError
    if card.face_storage_key is not None:
        # 여러 번 눌러도 같은 결과여야 합니다.
        return card

    key = build_card_face_key(card.app_user_id, card.id)
    stored = get_storage().stat(key)
    if stored is None:
        raise DogCardConflictError("face_not_uploaded", "업로드된 얼굴 그림을 찾을 수 없습니다.")
    if stored.size_bytes <= 0 or stored.size_bytes > MAX_CARD_FACE_BYTES:
        raise DogCardConflictError(
            "invalid_face_size",
            f"얼굴 그림은 비어 있지 않은 {MAX_CARD_FACE_BYTES // (1024 * 1024)} MiB "
            "이하 파일이어야 합니다.",
        )

    card.face_storage_key = key
    card.face_generation = stored.generation
    card.face_size_bytes = stored.size_bytes
    await session.commit()
    return card


async def list_cards(session: AsyncSession, app_user_id: uuid.UUID) -> list[DogCard]:
    """내 카드 전부. **폰을 바꿨을 때 복원**에 쓰는 목록입니다."""
    return await card_repo.list_for_owner(session, app_user_id)


async def get_card(
    session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID
) -> tuple[DogCard, str | None]:
    """카드 하나와 얼굴 그림 주소. 얼굴이 없으면 주소는 None 입니다."""
    from daengs_backend.config import settings

    card = await card_repo.get_owned(session, app_user_id, card_id)
    if card is None:
        raise DogCardNotFoundError

    url = None
    if card.face_storage_key is not None:
        url = get_storage().download_url(
            card.face_storage_key,
            expires_in_seconds=settings.gait_download_url_ttl_seconds,
            generation=card.face_generation,
            bridge_download_path=CARD_BRIDGE_DOWNLOAD_PATH,
        )
    return card, url


async def delete_card(
    session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID
) -> None:
    """카드 하나를 지웁니다. **얼굴 그림까지 지웁니다.**"""
    card = await card_repo.get_owned(session, app_user_id, card_id, for_update=True)
    if card is None:
        raise DogCardNotFoundError

    if card.face_storage_key is not None:
        # **객체를 먼저 지웁니다.** 행을 먼저 지우면 키를 잃어 파일이 영구 고아입니다.
        get_storage().delete(card.face_storage_key)

    await card_repo.delete(session, card)
    await session.commit()


async def cleanup_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴가 부릅니다. **얼굴 그림을 지우고 행을 지웁니다.**

    ⚠️ `app_users` 행은 탈퇴해도 남으므로 FK CASCADE 가 영영 안 돕니다 —
       대화(chats)·피부 기록과 같은 자리입니다.

    지울 그림이 아예 없으면 저장소를 안 건드립니다 — 저장소가 꺼져 있다고 탈퇴가
    막히면 안 됩니다.
    """
    cards = await card_repo.list_for_owner_for_update(session, app_user_id)
    keys = [c.face_storage_key for c in cards if c.face_storage_key]
    if keys:
        storage = get_storage()
        for key in keys:
            storage.delete(key)
    return await card_repo.delete_all_for_owner(session, app_user_id)
