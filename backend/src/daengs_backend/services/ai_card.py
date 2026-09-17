"""앱 사용자 AI 도감 카드의 규칙. 트랜잭션 경계도 여기입니다 (`/app/ai-cards/*`, #537, D-076).

**비동기입니다.** `start` 는 돈이 나가기 전에 거를 수 있는 것(닫힌 달·키·저장소·사진·탈퇴·
남의 강아지·한도)을 전부 동기로 거른 뒤, **이 요청에서 나올 행을 전부** `generating` 으로
커밋하고 바로 돌아갑니다(#572 Task 4 — 한 요청에 여러 장, fix round 1 controller ruling A:
행을 나중에 하나씩 만들지 않고 미리 다 만듭니다). POST 는 토큰만 확인하고
(`CurrentAppMemberTokenOnly`) **사진을 다 받은 뒤** `start` 가 사용자 행을 잠급니다 — 잠금 →
한도 → INSERT(전부) → commit 이 짧은 한 트랜잭션입니다. 생성은 같은 backend 프로세스 안의
백그라운드 작업(`_run`)이 그 행들을 **하나씩 순서대로** 채우고, 끝날 때마다 **새 세션으로**
그 행을 `ready`/`failed` 로 바꿉니다. 한도 규칙은 `services/ai_card_quota.py` 가 정하고
(D-077 · D-084), `ai_card_usage` 에는 **요청마다 한 줄**만 남깁니다 — 처음 슬롯을 잡을 때(유료
호출 전) 시도 표시, 닮음이 기준 이상인 카드가 처음 `ready` 가 되면 그것을 사용 기록으로 바꿉니다
(카드 수가 아니라 요청 수를 셉니다).

**행이 몇 개인지는 엔진이 정합니다** (#572 Task 8, 사용자 결정 2026-09-17 — 판정은
`ai_card_engine.gpu_path_active` 한 곳). Nano Banana 2 경로(`DAENGS_CARDGEN_URL` 빈 값, 지금 운영)는
한 요청에 **한 장**이고 seed 없이 불러 `generate_card` 의 재시도(닮음 미달이면 한 번 더)가 돕니다 — 앱에
두 장 중 고르는 화면이 아직 없기 때문입니다. `FLUX.2-klein-4B` GPU 경로는 `cardimage_pick_count` 장
(최대 2)을 행마다 미리 뽑은 seed 로, 재시도 없이 만듭니다.

⚠️ **백그라운드는 요청 세션을 쓰지 않습니다** — 요청이 끝나면 그 세션은 닫힙니다.
⚠️ **배포 재시작과 겹친 작업은 사라집니다.** 행은 `stale_after()` 가 지난 뒤 조회에서
   `failed`/`interrupted` 가 됩니다. 그것이 실제로 자주 보이면 워커로 옮길 때입니다 (D-076).
⚠️ **생성 중에 행이 지워질 수 있습니다**(삭제·탈퇴). 세마포어를 기다리는 동안 그리 됐으면
   `_claim_slot` 이 돈이 나가는 호출(엔진) 전에 멈춥니다. 끝난 작업은 행이 없거나 이미
   `generating` 이 아니면 방금 쓴 객체를 지웁니다 — 안 그러면 FK 없는 저장소에 영구 고아가
   남습니다. **행이 여럿이므로 카드 하나의 취소·실패가 다른 행의 시도를 막지 않습니다** —
   다만 `delete_card` 는 지우는 행이 속한 요청의 아직 `generating` 인 형제도 함께 지웁니다
   (fix round 1 Critical) — 안 그러면 취소한 뒤에도 나머지 카드가 계속 만들어집니다.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
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
from daengs_backend.services.ai_card_quota import (
    AiCardBusyError,
    check_quota,
    daily_remaining,
    kst_day_start,
    stale_after,
)
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


class AiCardNotReadyError(Exception):
    """`ready` 가 아닌 카드를 고르려 했습니다(#572 Task 4 fix round 2 R2-3). 라우터가 409 로 바꿉니다.

    `generating`·`failed` 카드를 고르면(예: 목록에서 아직 안 끝난 카드를 잘못 눌렀을 때) 형제
    (그중 이미 `ready` 인 좋은 카드일 수 있습니다)를 지워 버려 사용자에게 좋은 카드가 하나도
    안 남을 수 있습니다 — 그래서 `ready` 인 카드만 고를 수 있습니다.
    """


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
    title_name: str | None = None,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> AiCard:
    """카드 만들기를 시작합니다. **이 요청에서 나올 행을 전부 지금 만듭니다** (#572 Task 4 fix
    round 1 controller ruling A) — 나중에 백그라운드가 만드는 게 아닙니다. 그래서 사용자가 그
    직후 대표 행을 지우면(취소) 나머지 행도 이 자리에서 이미 존재하므로 함께 지워집니다
    (`delete_card`) — 백그라운드가 뒤늦게 형제 카드를 만들어 취소를 무시하는 일이 없습니다.
    """
    name = " ".join(dog_name.split())
    # 제목에만 쓰는 이름 (#543). 비면 `dog_name` 그대로 — `dog_name` 은 늘 그대로 저장합니다.
    title_source = " ".join((title_name or "").split()) or name
    # ── DB 전: 설정·사진. 여기서 걸리면 잠금도 연결도 안 잡습니다.
    meta = ai_card_engine.ready_check(month)
    if isinstance(get_storage(), NotConfiguredStorage):
        raise StorageNotConfiguredError("AI 카드 저장소가 설정되지 않았습니다 (GAIT_STORAGE)")
    photo_jpeg = await asyncio.to_thread(prepare_photo, photo, content_type)

    # ── 짧은 한 트랜잭션: 사용자 잠금 → 강아지 → 한도 → INSERT(전부) → commit.
    # 잠금이 **이 세션의 첫 문장**입니다 — 탈퇴와 직렬화되고, 업로드 동안에는 잡지 않습니다.
    if await app_user_repo.get_active_for_update(session, app_user_id) is None:
        raise AiCardUserNotActiveError
    if dog_id is not None and await pet_repo.get_accessible(session, app_user_id, dog_id) is None:
        raise AiCardNotFoundError

    now = now or datetime.now(UTC)
    await check_quota(
        session, app_user_id, now=now, daily_limit=settings.cardimage_daily_limit, dog_id=dog_id, month=month
    )

    title = title_text(meta.card_name, title_source)[:_TITLE_MAX]  # ß → SS 처럼 자를 수 있다.
    # 장수는 엔진이 정합니다(#572 Task 8, 사용자 결정 2026-09-17) — `plan_request_seeds` 의 길이가 곧
    # 행 수입니다. Nano Banana 2 경로(지금 운영)는 `[None]`: 한 장, seed 없이 불러 재시도가 돕니다(앱에
    # 두 장 중 고르는 화면이 아직 없습니다). GPU 경로(`FLUX.2-klein-4B`)는 장마다 쓸 seed 를 지금 한 번에
    # 뽑습니다 — 이 달의 겹치지 않는 seed 가 설정값보다 적으면(fix round 1 Important 2) 행도 그만큼만
    # 만듭니다. 같은 seed 로 두 번 만들면(엔진이 결정적이다) 완전히 같은 이미지 두 장에 돈을 두 번 냅니다.
    seeds = ai_card_engine.plan_request_seeds(month, rng or random.Random())
    # 요청의 대표 행은 **자기 id 를 pick_group 으로 씁니다** — `idx_ai_cards_one_generating` 이
    # 그 한 행만 보고 「사용자별 동시 1요청」을 지키게 하기 위해서입니다(fix round 1 Critical).
    primary_id = uuid.uuid4()
    cards = [
        AiCard(
            id=primary_id if i == 0 else uuid.uuid4(),
            app_user_id=app_user_id,
            dog_id=dog_id,
            month=month,
            dog_name=name,
            title=title,
            status="generating",
            seed=seed,
            pick_group=primary_id,
            created_at=now,
            updated_at=now,
        )
        for i, seed in enumerate(seeds)
    ]
    try:
        for card in cards:
            ai_card_repo.add(session, card)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        detail = str(exc.orig) if exc.orig is not None else str(exc)
        if "idx_ai_cards_one_generating" in detail:
            # 한도 검사를 둘 다 통과한 동시 요청 — 부분 UNIQUE 가 막았습니다. 다른 제약 위반은 그대로 올립니다.
            raise AiCardBusyError from None
        raise

    _spawn(_run([c.id for c in cards], seeds, app_user_id, photo_jpeg, month, title_source))
    return cards[0]


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


async def _claim_slot(card_id: uuid.UUID, *, mark_attempt: bool) -> bool:
    """세마포어를 얻은 뒤, 돈이 나가는 호출(엔진) **직전**에 행을 다시 봅니다.

    대기열에 있는 동안 행이 지워졌거나(삭제·탈퇴) 이미 다른 경로로 끝났으면(`generating` 이
    아니면) 엔진을 부르지 않고 멈춥니다 — "쓸 수 있는 것을 전부 돈이 나가기 전에 거른다"는
    원칙이 큐 대기까지 지켜야 하기 때문입니다. 살아 있으면 `updated_at` 을 지금으로 찍습니다:
    정리 기준(`stale_after`)은 **그 요청에서 가장 최근에 찍힌 이 칸부터** 잽니다
    (`ai_card_repo.expire_generating`, D-084) — 아직 도는 작업을 다른 조회가 가로채 실패로 덮지
    않고, 이 카드가 도는 동안 차례를 기다리는 같은 요청의 다음 카드도 덮지 않습니다.

    `mark_attempt=True`(요청에서 처음 슬롯을 잡을 때 — `_run` 이 정합니다)면 **같은 트랜잭션에서**
    요청의 시도 표시를 남깁니다(D-084). 유료 호출보다 먼저 커밋되므로, 호출 도중에 사용자가 카드를
    지워도 표시는 남아 돈 나간 시도 상한이 그 요청을 셉니다 — 시작→삭제를 되풀이해 유료 호출을
    끝없이 부르는 길을 막습니다.
    """
    async with _session_factory() as session:
        card = await ai_card_repo.get_for_update(session, card_id)
        if card is None or card.status != "generating":
            await session.rollback()
            return False
        now = datetime.now(UTC)
        card.updated_at = now
        if mark_attempt:
            await ai_card_repo.add_attempt_mark(
                session, card.pick_group or card.id, card.app_user_id, marked_at=now
            )
        await session.commit()
        return True


async def _run(
    card_ids: list[uuid.UUID],
    seeds: list[int | None],
    app_user_id: uuid.UUID,
    photo_jpeg: bytes,
    month: int,
    title_name: str,
) -> None:
    """백그라운드 한 건. **예외를 밖으로 내지 않습니다** — 낼 곳이 없고, 행에 결과를 남깁니다.

    행은 **이미 전부 있습니다** — `start` 가 미리 만들었습니다(#572 Task 4 fix round 1 controller
    ruling A). 여기서는 그 행을 하나씩 순서대로 채웁니다: ①`_claim_slot` 으로 그 행이 아직
    `generating` 인지 재확인(돈이 나가는 호출 **직전** 마지막 방어선 — 사용자가 그 사이 지웠으면
    엔진을 부르지 않습니다) → ②`generate_card(seed=...)` 한 번 → ③결과를 그 행에 씁니다.
    카드 하나의 실패·취소가 **다음 카드 시도를 막지 않습니다** — 행마다 독립입니다.

    ②는 엔진마다 다릅니다(#572 Task 8 — `ai_card_engine.plan_request_seeds` 가 정한 `seeds`):
    - **GPU 경로(`FLUX.2-klein-4B`)** — 행마다 seed 를 못박았으니 재시도가 없습니다(카드 여러 장을
      만드는 것 자체가 재시도의 대안입니다). 유료 호출은 행마다 한 번입니다.
    - **Nano Banana 2 경로(지금 운영)** — 행은 하나, `seed=None` 이라 `generate_card` 가 첫 장의 닮음이
      `cardimage_judge_min` 미만이면 **같은 행 안에서** 한 번 더 만들고 나은 쪽을 돌려줍니다(`attempts`
      2). 유료 호출이 최대 두 번(엔진·검수 각각)이지만 슬롯은 한 번만 잡으므로 시도 표시도 첫 호출
      전에 한 번만 남고, 정리 기준(`stale_after`)의 예산 `4 × cardimage_timeout_ms + 60초` 가 바로
      이 두 번(엔진·검수 × 2)을 덮도록 잡힌 값입니다.

    한도 기록은 **요청마다 한 줄**입니다 (D-084). 처음 슬롯을 잡을 때(유료 호출 전) 시도 표시를
    남기고(`_claim_slot(mark_attempt=True)`), 닮음이 기준 이상인 카드(`_meets_judge_min`)가 처음
    `ready` 가 되면 `_finish_ready` 가 그 표시를 지우고 사용 기록으로 바꿉니다. 좋은 카드가 끝내 안
    나오면(미달·실패·삭제) 표시가 그대로 남습니다. 표시는 한 번만 남기므로, 좋은 카드 뒤의 카드가
    슬롯을 잡아도 표시가 되살아나지 않습니다.
    """
    usage_recorded = False
    attempt_marked = False
    # 요청 하나에 엔진·검수 하나 — 카드마다 새로 만들지 않는다(행마다 다시 만들면 호출별 상태
    # (예: HTTP 엔진의 `last_meta`)가 카드 사이에서 안 이어진다).
    engine = ai_card_engine.default_engine()
    judge = ai_card_engine.default_judge()
    try:
        async with _slot():
            for card_id, seed in zip(card_ids, seeds, strict=True):
                if not await _claim_slot(card_id, mark_attempt=not attempt_marked):
                    # 이 행이 사라졌거나(삭제·탈퇴) 이미 정리됐습니다 — 이 카드는 건너뜁니다
                    # (엔진을 부르지 않습니다, 돈이 나가지 않습니다). 다른 행은 독립이니 계속 시도합니다.
                    continue
                attempt_marked = True
                try:
                    generated = await asyncio.to_thread(
                        ai_card_engine.generate,
                        photo=photo_jpeg,
                        content_type="image/jpeg",
                        month=month,
                        # `generate_card` 는 이 이름을 그림 제목에만 쓴다 — `ai_cards.title` 과 같은 글자여야 한다 (#543).
                        dog_name=title_name,
                        engine=engine,
                        judge=judge,
                        seed=seed,
                    )
                except Exception as exc:
                    code = _error_code(exc)
                    if code == "internal":
                        log.exception("AI 카드 생성 실패 (card=%s)", card_id)
                    else:
                        log.warning("AI 카드 생성 실패 (card=%s, %s): %s", card_id, code, exc)
                    await _finish_failed(card_id, code)
                    continue

                key = build_ai_card_key(app_user_id, card_id)
                try:
                    stored = await asyncio.to_thread(_store_png, key, generated.png)
                except Exception:
                    log.exception("AI 카드 저장 실패 (card=%s)", card_id)
                    await _finish_failed(card_id, "storage")
                    continue

                record_usage = not usage_recorded and _meets_judge_min(generated)
                try:
                    updated = await _finish_ready(card_id, key, stored, generated, record_usage=record_usage)
                except Exception:
                    # 객체는 저장됐는데 행을 못 바꿨습니다. 키를 아는 곳이 여기뿐이라 지우지 않으면 영구 고아입니다.
                    # 행은 `generating` 으로 남고 정리 기준이 지나면 `interrupted` 가 됩니다.
                    log.exception("AI 카드 완료 기록 실패 — 저장한 객체를 지웁니다 (card=%s)", card_id)
                    try:
                        await asyncio.to_thread(get_storage().delete, key)
                    except Exception:
                        log.exception("AI 카드 고아 객체 삭제도 실패했습니다 (card=%s, key=%s)", card_id, key)
                    continue
                if updated and record_usage:
                    usage_recorded = True
    except Exception:
        log.exception("AI 카드 백그라운드 작업이 정리 중에 실패했습니다 (cards=%s)", card_ids)


def _meets_judge_min(generated: GeneratedCard) -> bool:
    """이 카드가 하루 한도를 쓰는 「좋은 뽑기」인가 (D-084). 닮음이 `cardimage_judge_min` 이상이면 참.

    **검수 점수가 없으면(`judge is None` — 검수 장애·검수 없음) 참입니다.** 카드는 멀쩡할 수 있고,
    검수 장애가 하루 한도를 안 쓰는 공짜 무한 생성이 되면 안 됩니다.
    """
    return generated.judge is None or generated.judge.likeness >= settings.cardimage_judge_min


async def _finish_ready(
    card_id: uuid.UUID,
    key: str,
    stored: StoredObject,
    generated: GeneratedCard,
    *,
    record_usage: bool,
) -> bool:
    """이 행을 `ready` 로 채웁니다. **실제로 채웠으면 `True`.**

    행이 그 사이 사라졌거나(삭제·탈퇴) 이미 `generating` 이 아니면(정리 기준 초과 등) 방금 만든
    객체를 지우고 `False` 를 돌려줍니다 — 고아를 남기지 않습니다. 기록은 부르는 쪽(`_run`)이 정한
    대로 **같은 트랜잭션에서** 남깁니다 (D-084).

    `record_usage=True`(이 요청에서 처음 닮음 기준 이상인 카드)면 하루 한도 사용 기록(`card_id` = 이
    카드)을 남기고, 같은 요청의 시도 표시(`_claim_slot` 이 남긴 것)를 지웁니다 — 그 요청은 이제 좋은
    카드를 못 얻은 시도가 아니라 좋은 뽑기입니다. `False` 면 기록을 건드리지 않습니다(미달 카드는
    시도 표시가 그대로 남습니다).
    """
    width, height = Image.open(io.BytesIO(generated.png)).size
    async with _session_factory() as session:
        card = await ai_card_repo.get_for_update(session, card_id)
        if card is None or card.status != "generating":
            # 생성 중에 지워졌거나(삭제·탈퇴) 정리 기준이 지나 실패로 덮였습니다. 객체를 남기지 않습니다.
            await asyncio.to_thread(get_storage().delete, key)
            await session.rollback()
            return False
        card.status = "ready"
        card.storage_key = key
        card.generation = stored.generation
        card.size_bytes = stored.size_bytes
        card.width, card.height = width, height
        card.likeness = generated.judge.likeness if generated.judge else None
        card.attempts = generated.attempts
        # Nano Banana 2 는 seed 인자를 받고도 무시합니다 — `generate_card` 가 안에서 뽑은 값
        # (`generated.seed`)을 그대로 저장하면 "이 카드는 이 seed 로 만들어졌다"는 거짓 기록이 되고,
        # 어느 엔진이 만들었는지 칸이 없어 나중에 가려낼 수도 없습니다(최종 리뷰 minor 3). GPU 엔진만
        # seed 를 실제로 씁니다. 판정은 엔진 선택·장수와 **같은 함수** `gpu_path_active` 입니다(#572 Task 8).
        card.seed = generated.seed if ai_card_engine.gpu_path_active() else None
        now = datetime.now(UTC)
        card.updated_at = now
        if record_usage:
            # **같은 트랜잭션에서** 사용 기록을 남깁니다 (#543, D-077). 카드를 지워도 이 줄은 남아
            # 하루 한도가 돌아오지 않습니다. 한 요청에 한 번뿐입니다 — 카드 수가 아니라 요청을 셉니다.
            # 표시를 **먼저** 지웁니다 — 대표 카드가 좋은 카드면 사용 기록의 card_id 도 pick_group 입니다.
            await ai_card_repo.delete_attempt_mark(session, card.pick_group or card.id)
            ai_card_repo.add_usage(
                session,
                AiCardUsage(card_id=card.id, app_user_id=card.app_user_id, used_at=now, unfulfilled_attempt=False),
            )
        await session.commit()
        return True


async def _finish_failed(card_id: uuid.UUID, code: str) -> bool:
    """이 행을 `failed` 로 채웁니다. 행이 이미 사라졌거나 `generating` 이 아니면 아무것도 안 하고 `False`."""
    async with _session_factory() as session:
        card = await ai_card_repo.get_for_update(session, card_id)
        if card is None or card.status != "generating":
            await session.rollback()
            return False
        card.status = "failed"
        card.error_code = code
        card.updated_at = datetime.now(UTC)
        await session.commit()
        return True


async def _expire_stale(session: AsyncSession, app_user_id: uuid.UUID, now: datetime) -> None:
    if await ai_card_repo.expire_generating(session, app_user_id, stale_before=now - stale_after(), now=now):
        await session.commit()


async def list_cards(session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime | None = None) -> list[AiCard]:
    """내 카드 전부, 최근 것부터. **이미지 주소는 안 싣습니다** — N 장마다 저장소를 두드리게 됩니다."""
    await _expire_stale(session, app_user_id, now or datetime.now(UTC))
    return await ai_card_repo.list_for_owner(session, app_user_id)


async def daily_status(
    session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[int | None, int | None]:
    """`(daily_limit, daily_remaining)`. 무제한이면 `(None, None)` — 앱이 막을지 정하는 값입니다."""
    limit = settings.cardimage_daily_limit
    remaining = await daily_remaining(session, app_user_id, now=now or datetime.now(UTC), daily_limit=limit)
    return (limit or None, remaining)


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


async def group_progress(
    session: AsyncSession, app_user_id: uuid.UUID, card: AiCard
) -> tuple[int | None, int | None, bool]:
    """`card` 가 속한 요청(`pick_group`)의 진행률 `(done, total, finished)` (#572 Task 4).

    `done` 은 지금까지 `ready` 로 끝난 장수, `total` 은 이 그룹에 **실제로 만들어진 행 수**
    (설정값이 아닙니다 — seed 가 모자란 달은 요청보다 적게 만들어질 수 있습니다, fix round 1
    Important 2).

    ⚠️ **클라이언트는 `done == total` 이 아니라 `finished` 로 멈춰야 합니다** (fix round 1
    Important 1). 카드는 한 번에 하나씩 순서대로 만들어지므로 `done` 은 0에서 서서히 오르고,
    카드가 실패하면 `done` 이 `total` 에 영영 못 미칠 수 있습니다. `finished` 는 "이 그룹에
    아직 `generating` 인 행이 하나도 없다" 는 뜻이고, 성공이든 실패든 상관없이 더 나올 카드가
    없으면 참입니다.

    `pick_group` 이 없으면(마이그레이션 이전의 옛 행) `(None, None, True)` — 그룹이 없다는 것
    자체가 더 기다릴 것도 없다는 뜻입니다.
    """
    if card.pick_group is None:
        return None, None, True
    total, done, generating = await ai_card_repo.group_counts(session, app_user_id, card.pick_group)
    return done, total, generating == 0


async def choose_card(
    session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID
) -> tuple[AiCard, str | None]:
    """`card_id` 를 남기고, 같은 요청(`pick_group`)에서 나온 형제 카드를 지웁니다(#572 Task 4).

    **`ready` 인 카드만 고를 수 있습니다** (fix round 2 R2-3) — `generating`·`failed` 카드를
    고르면 형제(이미 `ready` 인 좋은 카드일 수 있습니다)를 지워 버려 사용자에게 카드가 하나도
    안 남을 수 있습니다. 형제가 없으면(단일 생성·옛 카드) 고른 카드를 그대로 돌려줍니다.
    **객체를 먼저 지웁니다** — `delete_card` 와 같은 순서(행을 먼저 지우면 키를 잃어 파일이
    영구 고아입니다).
    """
    card = await ai_card_repo.get_owned(session, app_user_id, card_id, for_update=True)
    if card is None:
        raise AiCardNotFoundError
    if card.status != "ready":
        await session.rollback()
        raise AiCardNotReadyError
    if card.pick_group is not None:
        siblings = await ai_card_repo.list_siblings(session, app_user_id, card.pick_group, exclude_id=card.id)
        storage = get_storage()
        for sibling in siblings:
            if sibling.storage_key is not None:
                storage.delete(sibling.storage_key)
            await ai_card_repo.delete(session, sibling)
    await session.commit()
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
    """카드 하나를 지웁니다. **생성 중이어도 지웁니다** — 끝난 작업이 객체를 치웁니다.

    **지우는 카드 자신이 `generating` 일 때만** 같은 요청(`pick_group`)의 아직 `generating` 인
    형제도 함께 지웁니다(#572 Task 4 fix round 1 Critical, fix round 2 R2-2). 취소는 "아직 진행
    중인 요청을 그만둔다" 는 뜻이라 이 조건이 필요합니다 — 지우는 카드가 이미 `ready` 라면
    그 요청은 **이미 기록이 남았으므로**(첫 슬롯에서 남긴 시도 표시, 기준 이상 카드가 나왔으면 사용
    기록 — D-084, 둘 다 카드를 지워도 남습니다), 형제를 지우지 않아도 공짜로 돌아오는
    것이 없고 `month_taken` 도 그대로입니다. 오히려 형제를
    지우면 사용자에게 카드가 하나도 안 남을 수 있으므로(#572 Task 4 fix round 2 재검토) 지우지
    않습니다 — 이미 끝난(`ready`·`failed`) 형제는 그 카드를 지울 때만 건드립니다(독립된
    결과물입니다).
    """
    card = await ai_card_repo.get_owned(session, app_user_id, card_id, for_update=True)
    if card is None:
        raise AiCardNotFoundError
    if card.status == "generating" and card.pick_group is not None:
        siblings = await ai_card_repo.list_siblings(session, app_user_id, card.pick_group, exclude_id=card.id)
        for sibling in siblings:
            if sibling.status == "generating":
                # generating 이면 storage_key 가 없다(ai_cards_ready_set) — 지울 객체가 없다.
                await ai_card_repo.delete(session, sibling)
    if card.storage_key is not None:
        # **객체를 먼저 지웁니다.** 행을 먼저 지우면 키를 잃어 파일이 영구 고아입니다.
        get_storage().delete(card.storage_key)
    await ai_card_repo.delete(session, card)
    await session.commit()


async def cleanup_for_owner(session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime | None = None) -> int:
    """탈퇴가 부릅니다. 커밋은 탈퇴 트랜잭션이 합니다.

    지울 객체가 없으면 저장소를 안 건드립니다 — 저장소가 꺼져 있다고 탈퇴가 막히면 안 됩니다.
    사용 기록은 **KST 오늘 00:00 이전 것만** 지웁니다 (D-077) — 같은 카카오 계정으로 재로그인하면 같은
    `app_user_id` 라, 오늘 기록을 지우면 그날 하루 한도가 초기화됩니다.
    """
    cards = await ai_card_repo.list_for_owner_for_update(session, app_user_id)
    keys = [c.storage_key for c in cards if c.storage_key]
    if keys:
        storage = get_storage()
        for key in keys:
            storage.delete(key)
    deleted = await ai_card_repo.delete_all_for_owner(session, app_user_id)
    # 사용 기록도 명시로 지웁니다 — 카드와 FK 로 안 묶였고, app_users CASCADE 는 탈퇴에서 안 돕니다.
    # 오늘 것은 남깁니다: 재로그인(같은 app_user_id)으로 그날 한도가 초기화되면 안 됩니다.
    day_start = kst_day_start(now or datetime.now(UTC))
    await ai_card_repo.delete_usage_for_owner(session, app_user_id, before=day_start)
    return deleted
