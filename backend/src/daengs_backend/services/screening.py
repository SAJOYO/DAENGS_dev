"""피부 변화 기록의 규칙. 트랜잭션 경계도 여기입니다 (`/app/screening/*`, D-052).

흐름은 보행·점령지·프로필과 **같은 모양**입니다 — 티켓 → 앱이 저장소에 직접 PUT →
confirm. 저장소를 GCS 로 되돌려도 앱 코드가 안 바뀌게 하려는 것입니다.

다른 점 하나: **판정이 동기입니다.** 보행은 celery 워커로 뺐지만 스크리닝 모델은
사진 한 장에 CPU 0.6~3초라 그럴 만큼 길지 않습니다. 그래서 `confirm` 안에서 판정까지
끝내고 결과를 같이 저장합니다.

⚠️ **이벤트 루프를 막으면 안 됩니다.** 같은 프로세스에서 로그인도 `/life/ask` 도
   돕니다 (D-039). 옛 경로가 `run_in_threadpool` 을 쓰는 것과 같은 이유로 여기도
   스레드로 뺍니다.

⚠️ **사진을 판정 뒤에도 안 지웁니다.** 점령지는 판정이 끝나면 `redact` 로 0바이트를
   덮지만, 여기는 **사진 자체가 기록**입니다 — 지난 사진과 나란히 놓고 보는 것이 이
   기능입니다. 보관은 "탈퇴 시 지체 없이 파기" 하나뿐입니다 (D-052 B).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.storage import (
    UploadTicket,
    build_screening_photo_key,
    get_storage,
)
from daengs_backend.models import ScreeningRecord
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import screening as screening_repo
from daengs_backend.schemas.screening import ScreeningStartRequest

log = logging.getLogger(__name__)

#: 사진 한 장의 상한(바이트).
#:
#: 옛 경로(`daengs_screening/config.py` 의 `MAX_BYTES`)와 **같은 12 MiB** 입니다.
#: 앱이 이미 그 크기로 줄여 보내고 있어서(`screening/Photo.kt`) 다르게 두면
#: 새 경로로 옮길 때 "옛 경로에서는 되던 사진이 안 된다" 가 됩니다.
MAX_SCREENING_PHOTO_BYTES = 12 * 1024 * 1024

#: 사진 bridge 의 경로. 도메인마다 다릅니다.
SCREENING_BRIDGE_UPLOAD_PATH = "/app/screening/_bridge/upload"
SCREENING_BRIDGE_DOWNLOAD_PATH = "/app/screening/_bridge/download"


class ScreeningNotFoundError(Exception):
    """내 기록이 아니거나 없습니다.

    **남의 것일 때도 이 예외입니다.** 403 으로 나누면 "그 id 는 존재한다"를 알려
    주는 셈이라, 없는 것과 남의 것을 같은 404 로 뭉갭니다 (pet 과 같은 규칙).
    """


class ScreeningConflictError(Exception):
    """기록 상태가 요청과 안 맞습니다. 라우터가 409 로 바꿉니다."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ScreeningModelUnavailableError(Exception):
    """가중치가 없거나 못 읽습니다. 라우터가 503 으로 바꿉니다.

    **요청이 잘못된 게 아닙니다** — 서버에 가중치를 놓아야 풀립니다
    (`SCREENING_RELEASE_DIR`).
    """


async def start_record(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    body: ScreeningStartRequest,
) -> tuple[ScreeningRecord, UploadTicket]:
    """기록 한 줄을 열고 사진 올릴 자리를 발급합니다.

    **행을 먼저 만듭니다.** 티켓만 주고 행을 나중에 만들면, 업로드는 됐는데 그 키가
    무엇인지 아무도 모르는 파일이 볼륨에 남습니다 — 저장소에는 FK 가 없어서
    아무도 안 치웁니다.

    ⚠️ **아이를 지정해도 대표만입니다 — `pet_repo.get_owned`.** `gait_records` 는
    `pet_id → pets.app_user_id` 로 소유가 유도되어 생성을 구성원(대표 ∪ 돌보미)으로
    열어도 대표가 그대로 봅니다. `screening_records` 는 다릅니다 — 소유가 만든 사람
    (`ScreeningRecord.app_user_id`)에 **직접** 저장되고 `repositories/screening.py` 는
    `pet_repo.member_condition` 을 쓴 적이 없습니다. 돌보미의 생성을 열면 대표가
    **못 보는** 스크리닝 기록이 생깁니다 — 한 집의 피부 이력이 둘로 쪼개지는데 어느
    쪽도 전체를 못 봅니다. 닫아 두는 쪽이 최소한 하나로 모인 이력을 지킵니다. 이
    레포지토리를 구성원 기준으로 다시 짜는 결정이 먼저이고, 그것은 이 카드의 범위
    밖입니다(docs/co-care.md §2, Task 12 follow-up).
    """
    # 남의 아이에 기록을 붙일 수 없습니다. FK 는 "존재하는 pets 행" 까지만 보장하고
    # 그게 내 것인지는 안 봅니다 (05_pets.sql 주석과 같은 자리).
    if (
        body.pet_id is not None
        and await pet_repo.get_owned(session, app_user_id, body.pet_id) is None
    ):
        raise ScreeningNotFoundError

    record_id = uuid.uuid4()
    object_key = build_screening_photo_key(
        app_user_id, record_id, content_type=body.content_type
    )

    ticket = get_storage().create_upload_ticket(
        object_key=object_key,
        content_type=body.content_type,
        bridge_upload_path=SCREENING_BRIDGE_UPLOAD_PATH,
        # 같은 티켓으로 두 번 못 올립니다. 키가 새도 살아 있는 객체를 못 덮습니다.
        create_only=True,
    )

    record = ScreeningRecord(
        id=record_id,
        app_user_id=app_user_id,
        pet_id=body.pet_id,
        status="PENDING_UPLOAD",
        photo_storage_key=object_key,
        photo_content_type=body.content_type,
        box=body.box,
    )
    screening_repo.add(session, record)
    await session.commit()
    return record, ticket


def _run_model(image_bytes: bytes, box: list[float] | None) -> tuple[dict, str]:
    """모델을 돌립니다. **동기 함수입니다** — 부르는 쪽이 스레드로 뺍니다.

    옛 경로(`daengs_screening/service.py`)와 **같은 함수**(`agent.screen`)를 씁니다.
    두 경로가 다른 답을 내면 "옛 화면과 새 화면이 다르게 말하는" 일이 생깁니다.
    """
    import io

    from PIL import Image

    from daengs_screening.service import CONTRACT_VERSION, _agent

    try:
        agent = _agent()
    except Exception as exc:  # 가중치 없음 · 손상
        raise ScreeningModelUnavailableError(str(exc)) from exc

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception as exc:
        raise ScreeningConflictError(
            "not_an_image", "이미지로 열리지 않는 파일입니다."
        ) from exc

    return agent.screen(image, box), CONTRACT_VERSION


async def confirm_record(
    session: AsyncSession, app_user_id: uuid.UUID, record_id: uuid.UUID
) -> ScreeningRecord:
    """올라온 사진을 확인하고 **판정까지 끝냅니다.**

    판정 실패는 `FAILED` 로 남깁니다 — 사진은 이미 저장소에 있으므로 행을 지우면
    그 파일이 고아가 됩니다. 사용자는 다시 찍으면 되고, 그건 새 기록입니다.
    """
    from starlette.concurrency import run_in_threadpool

    record = await screening_repo.get_owned(session, app_user_id, record_id, for_update=True)
    if record is None:
        raise ScreeningNotFoundError
    if record.status != "PENDING_UPLOAD":
        # 여러 번 눌러도 같은 결과여야 합니다. 이미 판정된 것을 다시 돌리면
        # 같은 사진에 다른 답이 남을 수 있습니다.
        return record

    storage = get_storage()
    stored = storage.stat(record.photo_storage_key)
    if stored is None:
        raise ScreeningConflictError("photo_not_uploaded", "업로드된 사진을 찾을 수 없습니다.")
    if stored.size_bytes <= 0 or stored.size_bytes > MAX_SCREENING_PHOTO_BYTES:
        raise ScreeningConflictError(
            "invalid_photo_size",
            f"사진은 비어 있지 않은 {MAX_SCREENING_PHOTO_BYTES // (1024 * 1024)} MiB "
            "이하 파일이어야 합니다.",
        )
    # 로컬 저장소는 content_type 을 안 돌려줍니다(None). 그때는 bridge 라우터가
    # PUT 헤더를 이미 대조했으므로 여기서 또 볼 것이 없습니다.
    if stored.content_type is not None and stored.content_type != record.photo_content_type:
        raise ScreeningConflictError(
            "photo_content_type_mismatch",
            "업로드된 파일의 Content-Type 이 발급된 형식과 다릅니다.",
        )

    record.photo_generation = stored.generation
    record.photo_size_bytes = stored.size_bytes

    # confirm 이 고정한 **그 바이트**만 읽습니다. 그 사이에 바뀌었으면 예외입니다.
    raw = await run_in_threadpool(
        storage.read_bytes,
        record.photo_storage_key,
        generation=stored.generation,
        max_bytes=MAX_SCREENING_PHOTO_BYTES,
    )

    try:
        # ⚠️ 사진 한 장에 CPU 0.6~3초입니다. 이벤트 루프를 막으면 그동안 로그인도
        #    /life/ask 도 멈춥니다 — 같은 프로세스이기 때문입니다 (D-039).
        result, contract_version = await run_in_threadpool(_run_model, raw, record.box)
    except ScreeningModelUnavailableError:
        # 서버에 가중치가 없는 것이라 **기록을 FAILED 로 만들지 않습니다** — 사용자
        # 잘못이 아니고, 가중치를 놓으면 같은 사진으로 다시 confirm 할 수 있습니다.
        await session.rollback()
        raise
    except ScreeningConflictError as exc:
        record.status = "FAILED"
        record.failure_reason = exc.detail[:2000]
        await session.commit()
        raise
    except Exception as exc:  # 무엇이 터지든 기록은 남깁니다
        record.status = "FAILED"
        record.failure_reason = str(exc)[:2000]
        await session.commit()
        log.exception("피부 판정 실패 record_id=%s", record_id)
        raise ScreeningConflictError(
            "screening_failed", "사진을 판정하지 못했어요. 다시 찍어 주세요."
        ) from exc

    record.result = result
    record.contract_version = contract_version
    record.status = "DONE"
    await session.commit()
    return record


async def list_records(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    *,
    pet_id: uuid.UUID | None = None,
) -> list[ScreeningRecord]:
    """내 기록을 최근 순으로. `pet_id` 를 주면 그 아이 것만 봅니다."""
    if pet_id is not None and await pet_repo.get_owned(session, app_user_id, pet_id) is None:
        raise ScreeningNotFoundError
    return await screening_repo.list_for_owner(session, app_user_id, pet_id=pet_id)


async def get_record(
    session: AsyncSession, app_user_id: uuid.UUID, record_id: uuid.UUID
) -> tuple[ScreeningRecord, str | None]:
    """기록 하나와 사진 주소. 아직 안 올라왔으면 주소는 None 입니다."""
    from daengs_backend.config import settings

    record = await screening_repo.get_owned(session, app_user_id, record_id)
    if record is None:
        raise ScreeningNotFoundError

    url = None
    if record.status != "PENDING_UPLOAD":
        url = get_storage().download_url(
            record.photo_storage_key,
            expires_in_seconds=settings.gait_download_url_ttl_seconds,
            generation=record.photo_generation,
            bridge_download_path=SCREENING_BRIDGE_DOWNLOAD_PATH,
        )
    return record, url


async def delete_record(
    session: AsyncSession, app_user_id: uuid.UUID, record_id: uuid.UUID
) -> None:
    """기록 하나를 지웁니다. **사진 파일까지 지웁니다.**"""
    record = await screening_repo.get_owned(session, app_user_id, record_id, for_update=True)
    if record is None:
        raise ScreeningNotFoundError

    # **객체를 먼저 지웁니다.** 행을 먼저 지우면 키를 잃어 파일이 영구 고아입니다 —
    # 저장소에는 FK 가 없습니다. 여기서 실패하면 아무것도 안 바뀌고, 다시 부르면 됩니다.
    get_storage().delete(record.photo_storage_key)

    await screening_repo.delete(session, record)
    await session.commit()


async def cleanup_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴가 부릅니다. **사진 파일을 지우고 행을 지웁니다.**

    ⚠️ `app_users` 행은 탈퇴해도 남으므로 FK CASCADE 가 영영 안 돕니다 —
       대화(chats)가 같은 이유로 명시 삭제인 것과 같은 자리입니다. 여기를 빠뜨리면
       공개한 처리방침 4항("탈퇴 시 지체 없이 파기")을 못 지킵니다.

    지울 사진이 아예 없으면 저장소를 안 건드립니다 — 저장소가 꺼져 있다고
    탈퇴가 막히면 안 됩니다.
    """
    records = await screening_repo.list_for_owner_for_update(session, app_user_id)
    if records:
        storage = get_storage()
        for record in records:
            storage.delete(record.photo_storage_key)
    return await screening_repo.delete_all_for_owner(session, app_user_id)
