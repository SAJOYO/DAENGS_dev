"""앱 사용자 AI 도감 카드의 규칙. 트랜잭션 경계도 여기입니다 (`/app/ai-cards/*`, #537, D-076).

**비동기입니다.** `start` 는 돈이 나가기 전에 거를 수 있는 것(닫힌 달·키·저장소·사진·탈퇴·
남의 강아지·한도)을 전부 동기로 거른 뒤 행을 `generating` 으로 커밋하고 바로 돌아갑니다.
POST 는 토큰만 확인하고(`CurrentAppMemberTokenOnly`) **사진을 다 받은 뒤** `start` 가 사용자 행을
잠급니다 — 잠금 → 한도 → INSERT → commit 이 짧은 한 트랜잭션입니다. 생성은 같은
backend 프로세스 안의 백그라운드 작업(`_run`)이 하고, 끝나면 **새 세션으로** 행을 `ready`/`failed`
로 바꿉니다.

⚠️ **백그라운드는 요청 세션을 쓰지 않습니다** — 요청이 끝나면 그 세션은 닫힙니다.
⚠️ **배포 재시작과 겹친 작업은 사라집니다.** 행은 `stale_after()` 가 지난 뒤 조회에서
   `failed`/`interrupted` 가 됩니다. 그것이 실제로 자주 보이면 워커로 옮길 때입니다 (D-076).
⚠️ **생성 중에 행이 지워질 수 있습니다**(삭제·탈퇴). 세마포어를 기다리는 동안 그리 됐으면
   `_claim_slot` 이 돈이 나가는 호출(엔진) 전에 멈춥니다. 끝난 작업은 행이 없거나 이미
   `generating` 이 아니면 방금 쓴 객체를 지웁니다 — 안 그러면 FK 없는 저장소에 영구 고아가
   남습니다.
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
import weakref
from datetime import UTC, datetime

from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.core.database import SessionLocal
from daengs_backend.core.storage import (
    GcsStorage,
    LocalBridgeStorage,
    NotConfiguredStorage,
    StorageNotConfiguredError,
    StoredObject,
    build_ai_card_key,
    get_storage,
)
from daengs_backend.models import AiCard, AiCardUsage
from daengs_backend.repositories import ai_card as ai_card_repo
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.services import ai_card_engine
from daengs_backend.services.ai_card_quota import AiCardBusyError, check_quota, stale_after
from daengs_cardimage import CardImageUnavailable, GeneratedCard
from daengs_cardimage.engine import EngineError
from daengs_cardimage.photo import prepare_photo
from daengs_cardimage.title import title_text

log = logging.getLogger(__name__)

AI_CARD_BRIDGE_DOWNLOAD_PATH = "/app/ai-cards/_bridge/download"
AI_CARD_CONTENT_TYPE = "image/png"

#: 백그라운드가 새 세션을 여는 곳. 테스트가 가짜로 바꿉니다.
_session_factory = SessionLocal

#: 돌고 있는 작업의 참조. 쥐고 있지 않으면 `create_task` 결과가 GC 로 사라질 수 있습니다.
_tasks: set[asyncio.Task] = set()

#: 이벤트 루프마다 하나의 세마포어. 루프 밖에서 만들면 다른 루프에서 쓸 때 깨집니다.
_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()

#: 저장하는 제목의 최대 길이 — `ai_cards.title` 이 VARCHAR(80) 입니다.
_TITLE_MAX = 80


class AiCardNotFoundError(Exception):
    """내 카드(또는 내가 돌보는 강아지)가 아니거나 없습니다. **남의 것일 때도 이 예외입니다.**"""


class AiCardUserNotActiveError(Exception):
    """토큰은 맞지만 회원이 이제 active 가 아닙니다(탈퇴 등). 라우터가 401 `not_active` 로 바꿉니다.

    POST 가 `CurrentAppMemberTokenOnly` 라 요청 경계에서 active 를 안 봅니다 — `start` 가 사진을
    다 받은 뒤 잠그며 확인합니다 (`core/deps.py::current_app_member_token_only`).
    """


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slot = _slots.get(loop)
    if slot is None:
        slot = _slots[loop] = asyncio.Semaphore(settings.cardimage_concurrency)
    return slot


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def start(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    *,
    photo: bytes,
    content_type: str,
    month: int,
    dog_name: str,
    dog_id: uuid.UUID | None,
    now: datetime | None = None,
) -> AiCard:
    name = " ".join(dog_name.split())
    # ── DB 전: 설정·사진. 여기서 걸리면 잠금도 연결도 안 잡습니다.
    meta = ai_card_engine.ready_check(month)
    if isinstance(get_storage(), NotConfiguredStorage):
        raise StorageNotConfiguredError("AI 카드 저장소가 설정되지 않았습니다 (GAIT_STORAGE)")
    photo_jpeg = await asyncio.to_thread(prepare_photo, photo, content_type)

    # ── 짧은 한 트랜잭션: 사용자 잠금 → 강아지 → 한도 → INSERT → commit.
    # 잠금이 **이 세션의 첫 문장**입니다 — 탈퇴와 직렬화되고, 업로드 동안에는 잡지 않습니다.
    if await app_user_repo.get_active_for_update(session, app_user_id) is None:
        raise AiCardUserNotActiveError
    if dog_id is not None and await pet_repo.get_accessible(session, app_user_id, dog_id) is None:
        raise AiCardNotFoundError

    now = now or datetime.now(UTC)
    await check_quota(
        session, app_user_id, now=now, daily_limit=settings.cardimage_daily_limit, dog_id=dog_id, month=month
    )

    card = AiCard(
        id=uuid.uuid4(),
        app_user_id=app_user_id,
        dog_id=dog_id,
        month=month,
        dog_name=name,
        # `str.upper()` 는 글자 수를 늘릴 수 있습니다(ß → SS) — 40자 이름도 80자를 넘길 수 있어 자릅니다.
        title=title_text(meta.card_name, name)[:_TITLE_MAX],
        status="generating",
        created_at=now,
        updated_at=now,
    )
    try:
        ai_card_repo.add(session, card)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        detail = str(exc.orig) if exc.orig is not None else str(exc)
        if "idx_ai_cards_one_generating" in detail:
            # 한도 검사를 둘 다 통과한 동시 요청 — 부분 UNIQUE 가 막았습니다. 다른 제약 위반은 그대로 올립니다.
            raise AiCardBusyError from None
        raise

    _spawn(_run(card.id, app_user_id, photo_jpeg, month, name))
    return card


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, EngineError):
        return exc.code if exc.code in ("upstream", "no_image") else "upstream"
    if isinstance(exc, CardImageUnavailable):
        return "unavailable"
    if isinstance(exc, StorageNotConfiguredError):
        return "storage"
    return "internal"


def _store_png(key: str, data: bytes) -> StoredObject:
    storage = get_storage()
    if isinstance(storage, LocalBridgeStorage):
        storage.write_if_absent(key, data)
    elif isinstance(storage, GcsStorage):
        storage.upload_bytes(key, data, content_type=AI_CARD_CONTENT_TYPE)
    else:
        raise StorageNotConfiguredError("AI 카드 저장소가 설정되지 않았습니다 (GAIT_STORAGE)")
    stored = storage.stat(key)
    if stored is None:
        raise RuntimeError(f"저장 직후 객체가 보이지 않습니다: {key}")
    return stored


async def _claim_slot(card_id: uuid.UUID) -> bool:
    """세마포어를 얻은 뒤, 돈이 나가는 호출(엔진) **직전**에 행을 다시 봅니다.

    대기열에 있는 동안 행이 지워졌거나(삭제·탈퇴) 이미 다른 경로로 끝났으면(`generating` 이
    아니면) 엔진을 부르지 않고 멈춥니다 — "쓸 수 있는 것을 전부 돈이 나가기 전에 거른다"는
    원칙이 큐 대기까지 지켜야 하기 때문입니다. 살아 있으면 `updated_at` 을 지금으로 찍습니다:
    정리 기준(`stale_after`)은 **이 시각부터** 잽니다 — 세마포어 대기가 길어져도 그 시간만큼
    정리 기준이 부풀지 않고, 아직 도는 작업을 다른 조회가 가로채 실패로 덮지 않습니다.
    """
    async with _session_factory() as session:
        card = await ai_card_repo.get_for_update(session, card_id)
        if card is None or card.status != "generating":
            await session.rollback()
            return False
        card.updated_at = datetime.now(UTC)
        await session.commit()
        return True


async def _run(card_id: uuid.UUID, app_user_id: uuid.UUID, photo_jpeg: bytes, month: int, dog_name: str) -> None:
    """백그라운드 한 건. **예외를 밖으로 내지 않습니다** — 낼 곳이 없고, 행에 결과를 남깁니다."""
    try:
        async with _slot():
            if not await _claim_slot(card_id):
                # 큐에서 기다리는 동안 지워졌거나 이미 정리됐습니다 — 엔진을 부르지 않습니다.
                return
            try:
                generated = await asyncio.to_thread(
                    ai_card_engine.generate,
                    photo=photo_jpeg,
                    content_type="image/jpeg",
                    month=month,
                    dog_name=dog_name,
                    engine=ai_card_engine.default_engine(),
                    judge=ai_card_engine.default_judge(),
                )
            except Exception as exc:
                code = _error_code(exc)
                if code == "internal":
                    log.exception("AI 카드 생성 실패 (card=%s)", card_id)
                else:
                    log.warning("AI 카드 생성 실패 (card=%s, %s): %s", card_id, code, exc)
                await _finish_failed(card_id, code)
                return

            key = build_ai_card_key(app_user_id, card_id)
            try:
                stored = await asyncio.to_thread(_store_png, key, generated.png)
            except Exception:
                log.exception("AI 카드 저장 실패 (card=%s)", card_id)
                await _finish_failed(card_id, "storage")
                return

            try:
                await _finish_ready(card_id, key, stored, generated)
            except Exception:
                # 객체는 저장됐는데 행을 못 바꿨습니다. 키를 아는 곳이 여기뿐이라 지우지 않으면 영구 고아입니다.
                # 행은 `generating` 으로 남고 정리 기준이 지나면 `interrupted` 가 됩니다.
                log.exception("AI 카드 완료 기록 실패 — 저장한 객체를 지웁니다 (card=%s)", card_id)
                try:
                    await asyncio.to_thread(get_storage().delete, key)
                except Exception:
                    log.exception("AI 카드 고아 객체 삭제도 실패했습니다 (card=%s, key=%s)", card_id, key)
                return
    except Exception:
        log.exception("AI 카드 백그라운드 작업이 정리 중에 실패했습니다 (card=%s)", card_id)


async def _finish_ready(card_id: uuid.UUID, key: str, stored: StoredObject, generated: GeneratedCard) -> None:
    width, height = Image.open(io.BytesIO(generated.png)).size
    async with _session_factory() as session:
        card = await ai_card_repo.get_for_update(session, card_id)
        if card is None or card.status != "generating":
            # 생성 중에 지워졌거나(삭제·탈퇴) 정리 기준이 지나 실패로 덮였습니다. 객체를 남기지 않습니다.
            await asyncio.to_thread(get_storage().delete, key)
            await session.rollback()
            return
        card.status = "ready"
        card.storage_key = key
        card.generation = stored.generation
        card.size_bytes = stored.size_bytes
        card.width, card.height = width, height
        card.likeness = generated.judge.likeness if generated.judge else None
        card.attempts = generated.attempts
        now = datetime.now(UTC)
        card.updated_at = now
        # **같은 트랜잭션에서** 사용 기록을 남깁니다 (#543, D-077). 카드를 지워도 이 줄은 남아 하루 한도가
        # 돌아오지 않습니다. ready 가 못 된 카드(실패·중간 삭제)는 여기까지 안 오므로 세지 않습니다.
        ai_card_repo.add_usage(session, AiCardUsage(card_id=card.id, app_user_id=card.app_user_id, used_at=now))
        await session.commit()


async def _finish_failed(card_id: uuid.UUID, code: str) -> None:
    async with _session_factory() as session:
        card = await ai_card_repo.get_for_update(session, card_id)
        if card is None or card.status != "generating":
            await session.rollback()
            return
        card.status = "failed"
        card.error_code = code
        card.updated_at = datetime.now(UTC)
        await session.commit()


async def _expire_stale(session: AsyncSession, app_user_id: uuid.UUID, now: datetime) -> None:
    if await ai_card_repo.expire_generating(session, app_user_id, stale_before=now - stale_after(), now=now):
        await session.commit()


async def list_cards(session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime | None = None) -> list[AiCard]:
    """내 카드 전부, 최근 것부터. **이미지 주소는 안 싣습니다** — N 장마다 저장소를 두드리게 됩니다."""
    await _expire_stale(session, app_user_id, now or datetime.now(UTC))
    return await ai_card_repo.list_for_owner(session, app_user_id)


async def get_card(
    session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[AiCard, str | None]:
    await _expire_stale(session, app_user_id, now or datetime.now(UTC))
    card = await ai_card_repo.get_owned(session, app_user_id, card_id)
    if card is None:
        raise AiCardNotFoundError
    url = None
    if card.status == "ready" and card.storage_key is not None:
        url = get_storage().download_url(
            card.storage_key,
            expires_in_seconds=settings.gait_download_url_ttl_seconds,
            generation=card.generation,
            bridge_download_path=AI_CARD_BRIDGE_DOWNLOAD_PATH,
        )
    return card, url


async def delete_card(session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID) -> None:
    """카드 하나를 지웁니다. **생성 중이어도 지웁니다** — 끝난 작업이 객체를 치웁니다."""
    card = await ai_card_repo.get_owned(session, app_user_id, card_id, for_update=True)
    if card is None:
        raise AiCardNotFoundError
    if card.storage_key is not None:
        # **객체를 먼저 지웁니다.** 행을 먼저 지우면 키를 잃어 파일이 영구 고아입니다.
        get_storage().delete(card.storage_key)
    await ai_card_repo.delete(session, card)
    await session.commit()


async def cleanup_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴가 부릅니다. 커밋은 탈퇴 트랜잭션이 합니다.

    지울 객체가 없으면 저장소를 안 건드립니다 — 저장소가 꺼져 있다고 탈퇴가 막히면 안 됩니다.
    """
    cards = await ai_card_repo.list_for_owner_for_update(session, app_user_id)
    keys = [c.storage_key for c in cards if c.storage_key]
    if keys:
        storage = get_storage()
        for key in keys:
            storage.delete(key)
    return await ai_card_repo.delete_all_for_owner(session, app_user_id)
