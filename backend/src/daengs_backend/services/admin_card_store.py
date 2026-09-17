"""콘솔이 시험 삼아 뽑은 카드의 저장·조회·삭제 (#592, 표는 `admin_ai_cards`).

앱 경로(`services/ai_card.py`)와 **아무것도 공유하지 않습니다** — 하루 한도도, 동시 생성
세마포어도, 백그라운드 작업도 없습니다. 콘솔 생성은 동기라(라우터가 그 자리에서 기다립니다)
여기서는 다 만들어진 PNG 를 받아 저장소에 쓰고 행 하나를 남길 뿐입니다.

⚠️ **저장소가 꺼져 있어도 예외를 올리지 않습니다.** `save` 가 `None` 을 돌려주고, 라우터는
   카드 PNG 를 그대로 응답하면서 `stored=false` 만 싣습니다 (spec ④). 관리자가 엔진을 견주어
   보는 화면이라, 저장이 안 된다고 이미 돈이 나간 카드를 버리면 그 호출을 그냥 날립니다.

⚠️ **객체를 먼저 쓰고 행을 나중에 만듭니다.** 반대로 하면 키를 모르는 행이 생기고, 행을 못
   만들었을 때는 방금 쓴 객체를 지웁니다 — 키를 아는 곳이 여기뿐이라 두면 영구 고아입니다.
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from datetime import UTC, datetime

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.storage import (
    GcsStorage,
    LocalBridgeStorage,
    StorageNotConfiguredError,
    StoredObject,
    build_admin_ai_card_key,
    get_storage,
)
from daengs_backend.models import AdminAiCard
from daengs_backend.repositories import admin_ai_card as admin_ai_card_repo
from daengs_cardimage.judge import JudgeResult

log = logging.getLogger(__name__)

ADMIN_AI_CARD_CONTENT_TYPE = "image/png"

#: 한 번에 읽어 올리는 상한. 994×1582 PNG 는 2MB 안팎이라 넉넉합니다 — 상한 자체는 저장소
#: 계약(`read_bytes`)이 요구합니다. 바이트가 통째로 메모리에 올라오는 자리라서입니다.
_MAX_PNG_BYTES = 16 * 1024 * 1024

#: 표의 칸 길이 (`db/init/41_admin_ai_cards.sql`). 넘치면 자릅니다 — 시험 삼아 뽑는 화면이
#: 긴 이름 하나로 DataError 를 맞는 것보다 낫습니다.
_DOG_NAME_MAX = 40
_TITLE_MAX = 80
_JUDGE_NOTE_MAX = 200


def _store_png(key: str, png: bytes) -> StoredObject | None:
    """PNG 를 저장소에 씁니다. **저장소가 꺼져 있으면 예외가 아니라 `None`.**

    분기는 `services/ai_card.py::_store_png` 와 같습니다 (local 은 create-only 로, gcs 는 직접
    업로드). 다른 것은 `none` 일 때뿐입니다 — 앱 경로는 거기서 503 을 내지만, 콘솔은 저장만
    건너뛰고 카드를 보여 줍니다.
    """
    try:
        storage = get_storage()
    except StorageNotConfiguredError:
        # `local` 인데 경로·주소가 비었을 때도 여기로 옵니다. 콘솔에서는 꺼진 것과 같이 봅니다.
        log.warning("콘솔 카드 저장소 설정이 불완전합니다 — 저장을 건너뜁니다 (key=%s)", key)
        return None
    if isinstance(storage, LocalBridgeStorage):
        storage.write_if_absent(key, png)
    elif isinstance(storage, GcsStorage):
        storage.upload_bytes(key, png, content_type=ADMIN_AI_CARD_CONTENT_TYPE)
    else:
        log.info("콘솔 카드 저장소가 꺼져 있습니다 (GAIT_STORAGE) — 저장을 건너뜁니다 (key=%s)", key)
        return None
    stored = storage.stat(key)
    if stored is None:
        raise RuntimeError(f"저장 직후 객체가 보이지 않습니다: {key}")
    return stored


def _read_png(key: str) -> bytes | None:
    """저장된 바이트. 객체가 없거나 저장소가 꺼져 있으면 `None`.

    `generation` 을 칸에 안 들고 있으므로 `stat()` 으로 지금 값을 읽어 그대로 넘깁니다 — 앱
    카드와 달리 confirm 으로 고정한 남의 업로드가 아니라 **우리가 쓴 것**이라, 고정할 것이
    애초에 없습니다.
    """
    try:
        storage = get_storage()
        stored = storage.stat(key)
        if stored is None:
            return None
        return storage.read_bytes(key, generation=stored.generation, max_bytes=_MAX_PNG_BYTES)
    except StorageNotConfiguredError:
        log.warning("콘솔 카드 저장소가 꺼져 있어 이미지를 읽지 못했습니다 (key=%s)", key)
        return None


def _delete_png(key: str) -> None:
    """저장소에서 지웁니다. **이미 없는 객체도, 꺼진 저장소도 실패가 아닙니다** — 두 구현 모두
    없는 키의 삭제를 조용히 넘기고(`LocalBridgeStorage` 는 `exists()`, `GcsStorage` 는 NotFound),
    `none` 이면 여기서 막습니다."""
    try:
        get_storage().delete(key)
    except StorageNotConfiguredError:
        log.warning("콘솔 카드 저장소가 꺼져 있어 객체를 못 지웠습니다 (key=%s)", key)


async def save(
    session: AsyncSession,
    *,
    admin_user_id: uuid.UUID,
    card_key: str,
    dog_name: str,
    title: str,
    engine: str,
    seed: int | None,
    attempts: int,
    judge: JudgeResult | None,
    png: bytes,
    elapsed_ms: int,
) -> AdminAiCard | None:
    """만들어진 카드를 저장소와 표에 남깁니다. **저장소가 꺼져 있으면 `None`** (행도 안 만듭니다).

    `card_id` 를 여기서 뽑아 키를 먼저 정합니다 — 행을 만든 뒤에 키를 정하면 저장이 실패했을 때
    이미지 없는 행이 남습니다. 커밋은 여기서 합니다(트랜잭션 경계는 services, D-011).
    """
    card_id = uuid.uuid4()
    key = build_admin_ai_card_key(admin_user_id, card_id)
    stored = await asyncio.to_thread(_store_png, key, png)
    if stored is None:
        return None

    width, height = Image.open(io.BytesIO(png)).size
    card = AdminAiCard(
        id=card_id,
        admin_user_id=admin_user_id,
        card_key=card_key,
        dog_name=dog_name[:_DOG_NAME_MAX],
        title=title[:_TITLE_MAX],
        engine=engine,
        seed=seed,
        attempts=attempts,
        likeness=judge.likeness if judge is not None else None,
        judge_note=judge.note[:_JUDGE_NOTE_MAX] if judge is not None else None,
        storage_key=key,
        size_bytes=stored.size_bytes,
        width=width,
        height=height,
        elapsed_ms=elapsed_ms,
        # 칸의 기본값은 `NOW()` 지만 **여기서 못박습니다** — 서버 기본값은 commit 뒤 그 속성을
        # 읽는 순간 lazy load 를 일으키고, 비동기 세션에서 그것은 MissingGreenlet 입니다.
        created_at=datetime.now(UTC),
    )
    try:
        admin_ai_card_repo.add(session, card)
        await session.commit()
    except Exception:
        await session.rollback()
        log.exception("콘솔 카드 기록 실패 — 방금 저장한 객체를 지웁니다 (key=%s)", key)
        try:
            await asyncio.to_thread(_delete_png, key)
        except Exception:
            log.exception("콘솔 카드 고아 객체 삭제도 실패했습니다 (key=%s)", key)
        raise
    return card


async def recent(session: AsyncSession, limit: int = 50) -> list[AdminAiCard]:
    """최근 카드 목록. **누가 뽑았든 전부** 줍니다 — 콘솔은 관리자끼리 서로 보는 화면입니다
    (사용자 결정 09-18). 이미지 바이트는 안 싣습니다 — 목록 한 번에 N 장을 읽게 됩니다."""
    return await admin_ai_card_repo.list_recent(session, limit=limit)


async def load_png(session: AsyncSession, card_id: uuid.UUID) -> tuple[AdminAiCard, bytes] | None:
    """행과 PNG 바이트. **행이 없거나 객체가 사라졌으면 `None`** 입니다 (라우터가 404).

    콘솔은 앱의 bridge(무인증 다운로드)를 쓰지 않습니다 — 관리자 토큰으로 이 경로를 지나
    바이트를 받습니다 (spec ④).
    """
    card = await admin_ai_card_repo.get(session, card_id)
    if card is None:
        return None
    png = await asyncio.to_thread(_read_png, card.storage_key)
    if png is None:
        return None
    return card, png


async def remove(session: AsyncSession, card_id: uuid.UUID) -> bool:
    """행과 저장된 PNG 를 지웁니다. **행이 이미 없으면 `False`.**

    **객체를 먼저 지웁니다** — 행을 먼저 지우면 키를 잃어 파일이 영구 고아입니다
    (`services/ai_card.py::delete_card` 와 같은 순서). 이미 없는 객체를 지우는 것은 실패가
    아니라서, 객체만 먼저 사라진 행도 그대로 지워집니다.
    """
    card = await admin_ai_card_repo.get(session, card_id, for_update=True)
    if card is None:
        return False
    await asyncio.to_thread(_delete_png, card.storage_key)
    await admin_ai_card_repo.delete(session, card)
    await session.commit()
    return True
