"""진료비 기록의 규칙 — 동의 분기 · 멱등 세 층 · 초안 청소 (#353, docs/vet-visits.md §3).

라우터는 HTTP 만 보고, 리포지토리는 쿼리만 한다. "동의했나" · "같은 영수증인가" ·
"이 강아지가 내 것인가" · "언제 확정 행이 생기는가" 는 전부 여기 모인다. 트랜잭션
경계도 여기다 (`care_event.py` · `screening.py` 와 같은 자리).

**동의 분기는 `extract_draft` 에서 갈린다, `confirm_draft` 가 아니다.** `vet_visit_drafts.
extracted` 가 추출 결과를 최대 24시간 들고 있어서, confirm 까지 미루면 그 시간 동안
미동의 유저의 항목이 DB 에 앉아 있게 된다 (docs §3 "분기는 confirm 이 아니라
초안을 쓸 때 갈린다"). `confirm_draft` 는 `raw_ocr_items` 를 **초안에서만** 읽는다 —
요청 본문이 들고 있는 값을 믿으면 앱이 그 분기를 우회할 수 있다.

멱등은 세 층이다 (docs §3):
  ① `vet_visit_drafts` 의 `(app_user_id, client_event_id)` — 같은 화면에서 두 번 탭.
  ② `receipt_sha256` — 앱 재시작으로 새 키인데 같은 사진. **추출 전에** 본다.
  ③ `extracted_at IS NOT NULL` — extract 를 두 번 부름. Gemini 재호출 없음.
`vet_visits` 자신의 `(app_user_id, client_event_id)` 는 이 셋 뒤의 안전망이다 — 확정은
사람이 한 번 누르는 버튼이라 실제로 두 번 눌리는 자리가 아니다.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from daengs_backend.core.storage import UploadTicket, build_vet_receipt_key, get_storage
from daengs_backend.models import VET_REASON_CODES, VET_REASON_LABELS, VetVisit, VetVisitDraft
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import vet_visit as vet_repo
from daengs_backend.services import vet_receipt
from daengs_backend.services.vet_receipt import ReceiptExtraction, ReceiptExtractionFailed

log = logging.getLogger(__name__)

#: 확정 안 된 초안의 수명. 지나면 사진과 함께 지운다 (docs §1 "초안 청소").
DRAFT_TTL = timedelta(hours=24)
#: 요청 한 번의 청소 상한. Beat 가 없어 요청에 얹으므로, 몇 천 건을 한 번에 지우지 않는다.
SWEEP_LIMIT = 50
#: 영수증 사진 한 장의 상한(바이트). 피부 사진과 같은 12 MiB (`screening.py`) —
#: 다르게 둘 근거가 없다.
MAX_RECEIPT_BYTES = 12 * 1024 * 1024

#: 사진 bridge 의 경로. 도메인마다 다르다 (`screening.py` 와 같은 규칙).
VET_RECEIPT_BRIDGE_UPLOAD_PATH = "/app/vet-visits/_bridge/upload"
VET_RECEIPT_BRIDGE_DOWNLOAD_PATH = "/app/vet-visits/_bridge/download"

_CONTENT_TYPE_BY_SUFFIX = {".jpg": "image/jpeg", ".webp": "image/webp"}


class VetVisitNotFoundError(Exception):
    """내 것이 아니거나 없다. 남의 것일 때도 이 예외다 (`PetNotFoundError` 와 같은 이유)."""


class VetVisitConflictError(Exception):
    """기록 상태가 요청과 안 맞는다. 라우터가 409 로 바꾼다."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class VetRangeError(ValueError):
    """조회 창이 뒤집혔거나 상한을 넘었다. 라우터가 422 로 바꾼다 (`care_event.
    CareRangeError` 와 같은 자리)."""


#: 기간 조회의 상한. "피부로 1년간 얼마 썼나" 가 실제 질문이라 (docs 머리말),
#: 케어 로그(31일)보다 훨씬 넉넉하게 둔다.
MAX_RANGE = timedelta(days=366 * 5)
#: 안 보내면 최근 1년.
DEFAULT_RANGE = timedelta(days=366)


@dataclass(frozen=True)
class StartDraftRequest:
    """`start_draft` 의 입력. 라우터가 pydantic 요청 본문에서 이걸 만든다."""

    pet_id: uuid.UUID
    content_type: str
    client_event_id: uuid.UUID


@dataclass(frozen=True)
class ConfirmDraftRequest:
    """`confirm_draft` 의 입력. **`items` 를 받는 필드가 여기 없다** — `raw_ocr_items`
    는 초안에서만 읽는다 (docs §3). 여기 다시 `items` 를 더하고 싶어지면, HTTP 경계의
    `schemas.vet_visit.VetVisitConfirmRequest` 머리말이 이미 막아 둔 그 판단부터
    다시 보라는 뜻이다 — 받아도 안 쓰는 필드는 다음 편집이 집어 들 총이다."""

    client_event_id: uuid.UUID
    reason_code: str
    visited_on: date
    total_krw: int
    reason_detail: str | None = None
    hospital_name: str | None = None
    hospital_address: str | None = None
    hospital_phone: str | None = None
    is_emergency: bool = False
    is_oncology: bool = False


@dataclass(frozen=True)
class ReasonOption:
    """[edit] 드롭다운 한 줄. **코드와 표시명을 같이 낸다** — 앱이 17개 한글 표시명을
    하드코딩하면, 닫힌 목록을 서버가 지키는 이유(§1)가 그 하드코딩 자리에서 다시 샌다."""

    code: str
    label: str


@dataclass(frozen=True)
class DraftExtraction:
    """`extract_draft` 의 결과. `extraction` 은 **응답 전용이다** — 미동의여도 이 값에는
    항목이 실려 있다. 저장되는 것은 `draft.extracted` 뿐이고, 그쪽은 동의 분기를 거친다."""

    draft: VetVisitDraft
    status: Literal["ok", "unreadable", "failed"]
    unreadable_reason: str | None
    possible_duplicate: bool
    extraction: ReceiptExtraction | None


def content_type_from_key(key: str) -> str:
    """확장자로 되짚는 Content-Type. **공개 함수다** — `routers/vet_visit.py` 의
    bridge 업로드가 이 값으로 앱이 보낸 Content-Type 헤더를 대조한다(`VetVisitDraft`
    에는 그 값을 담을 칸이 따로 없다)."""
    for suffix, content_type in _CONTENT_TYPE_BY_SUFFIX.items():
        if key.endswith(suffix):
            return content_type
    return "image/jpeg"


def _ticket_for(draft: VetVisitDraft) -> UploadTicket:
    """멱등 ① 로 있던 초안을 돌려줄 때도 같은 모양의 티켓이 필요하다 — 앱은 늘
    `(draft, ticket, created)` 를 받는다."""
    return get_storage().create_upload_ticket(
        object_key=draft.receipt_image_key,
        content_type=content_type_from_key(draft.receipt_image_key),
        bridge_upload_path=VET_RECEIPT_BRIDGE_UPLOAD_PATH,
        create_only=True,
    )


async def _sweep_expired(session: AsyncSession) -> None:
    """24시간 지난 초안을 **사진과 함께** 지운다. Beat 가 없어 `start_draft` 에 얹는다
    (docs §1 "초안 청소") — 요청당 최대 `SWEEP_LIMIT` 건.

    ⚠️ 확정된 기록(`vet_visits`)은 사진 키를 초안에서 그대로 물려받고 초안 행은
       `confirm_draft` 가 지운다. 그래서 여기서 마주치는 초안은 **확정 안 된 것뿐**이고,
       사진을 지워도 다른 표가 참조하는 객체를 건드리지 않는다.
    """
    cutoff = datetime.now(UTC) - DRAFT_TTL
    expired = await vet_repo.expired_drafts(session, cutoff, limit=SWEEP_LIMIT)
    if not expired:
        return
    # **커밋을 먼저 한다.** 행을 지우기 전에 사진을 지우면, 커밋이 실패했을 때
    # 여전히 존재하는 초안의 사진이 이미 사라진 뒤다. 순서를 바꾸면 실패의 대가가
    # DB 행이 아니라 사용자의 사진이 된다.
    for draft in expired:
        await vet_repo.delete_draft(session, draft)
    await session.commit()
    storage = get_storage()
    for draft in expired:
        storage.delete(draft.receipt_image_key)


async def start_draft(
    session: AsyncSession, app_user_id: uuid.UUID, body: StartDraftRequest
) -> tuple[VetVisitDraft, UploadTicket, bool]:
    """초안 한 줄을 열고 사진 올릴 자리를 발급한다. **행을 먼저 만든다** — 티켓만 주고
    행을 나중에 만들면, 업로드는 됐는데 키를 아무도 모르는 파일이 볼륨에 남는다
    (`screening.start_record` 와 같은 이유).

    :returns: (초안, 업로드 티켓, 이번에 새로 만들었는가)
    """
    await _sweep_expired(session)

    existing = await vet_repo.get_draft_by_client_event(session, app_user_id, body.client_event_id)
    if existing is not None:
        # 멱등 ① — 얼어 보이는 화면에서 두 번 탭. 새 티켓도 새 행도 안 만든다.
        return existing, _ticket_for(existing), False

    if await pet_repo.get_owned(session, app_user_id, body.pet_id) is None:
        raise VetVisitNotFoundError

    draft_id = uuid.uuid4()
    object_key = build_vet_receipt_key(app_user_id, draft_id, content_type=body.content_type)
    ticket = get_storage().create_upload_ticket(
        object_key=object_key,
        content_type=body.content_type,
        bridge_upload_path=VET_RECEIPT_BRIDGE_UPLOAD_PATH,
        create_only=True,
    )

    draft = VetVisitDraft(
        id=draft_id,
        app_user_id=app_user_id,
        pet_id=body.pet_id,
        receipt_image_key=object_key,
        client_event_id=body.client_event_id,
    )
    vet_repo.add(session, draft)
    try:
        await session.commit()
    except IntegrityError:
        # 같은 키가 동시에 두 번 온 경쟁. 진 쪽이다 — 이긴 쪽의 행을 돌려준다.
        await session.rollback()
        winner = await vet_repo.get_draft_by_client_event(
            session, app_user_id, body.client_event_id
        )
        if winner is None:  # pragma: no cover — UNIQUE 가 터졌는데 행이 없을 수는 없다
            raise
        return winner, _ticket_for(winner), False
    return draft, ticket, True


def _validate_extraction_dict(fields: dict) -> ReceiptExtraction | None:
    try:
        return ReceiptExtraction.model_validate(fields)
    except Exception:  # noqa: BLE001 - 저장된 값이 옛 계약이면 확인 화면은 빈 채로 간다
        log.warning("초안에 저장된 추출 결과를 못 읽었다")
        return None


def _draft_extraction_from_payload(draft: VetVisitDraft, payload: dict) -> DraftExtraction:
    fields = {k: v for k, v in payload.items() if k != "possible_duplicate"}
    return DraftExtraction(
        draft=draft,
        status=payload.get("status", "failed"),
        unreadable_reason=payload.get("unreadable_reason"),
        possible_duplicate=bool(payload.get("possible_duplicate", False)),
        extraction=_validate_extraction_dict(fields),
    )


async def extract_draft(
    session: AsyncSession, app_user_id: uuid.UUID, draft_id: uuid.UUID
) -> DraftExtraction:
    """올라온 영수증 사진을 읽는다. **500 을 내지 않는다** — `ReceiptExtractionFailed` 는
    `status="failed"` 로, 모델이 "이 사진은 못 읽는다"고 답한 것은 `status="unreadable"`
    로 갈린다 (docs §2 "못 읽었을 때"). 둘 다 유저는 손으로 채워 확정할 수 있다.
    """
    draft = await vet_repo.get_draft_owned(session, app_user_id, draft_id)
    if draft is None:
        raise VetVisitNotFoundError

    if draft.extracted_at is not None:
        # 멱등 ③ — extract 를 두 번 부름. Gemini 재호출 없음.
        return _draft_extraction_from_payload(draft, dict(draft.extracted or {}))

    storage = get_storage()
    stored = storage.stat(draft.receipt_image_key)
    if stored is None:
        raise VetVisitConflictError("photo_not_uploaded", "업로드된 영수증 사진을 찾을 수 없습니다.")
    if stored.size_bytes <= 0 or stored.size_bytes > MAX_RECEIPT_BYTES:
        raise VetVisitConflictError(
            "invalid_photo_size",
            f"사진은 비어 있지 않은 {MAX_RECEIPT_BYTES // (1024 * 1024)} MiB 이하 파일이어야 합니다.",
        )

    raw = await run_in_threadpool(
        storage.read_bytes,
        draft.receipt_image_key,
        generation=stored.generation,
        max_bytes=MAX_RECEIPT_BYTES,
    )
    receipt_sha256 = sha256(raw).hexdigest()

    # 멱등 ② — 앱 재시작으로 새 키인데 같은 사진. **Gemini 를 부르기 전에** 본다.
    match = await vet_repo.get_draft_by_sha(session, app_user_id, receipt_sha256)

    # **동의는 경로와 무관하게, 쓰기 하나 앞에서 한 번만 본다.** 재사용 경로(멱등 ②)로
    # 왔다고 동의 확인을 건너뛰면, "동의 → 추출(항목 저장) → 동의 철회 → 같은 사진
    # 재업로드" 순서에서 옛 항목이 그대로 새 초안에, 그리고 곧 학습 코퍼스로 들어간다.
    app_user = await app_user_repo.get_by_id(session, app_user_id)
    consented = app_user is not None and app_user.ocr_consent_at is not None

    if match is not None and match.id != draft.id and match.extracted_at is not None:
        # 사진은 그대로 물려받지만 **`possible_duplicate` 는 다시 잰다** — 그 초안이
        # 추출됐던 시점과 지금 사이에 매칭되는 방문이 확정됐을 수 있다(#M3 real bug:
        # 예전에는 `match.extracted` 를 통째로 베껴 이 칸이 그때 값에 갇혔다).
        response_payload = dict(match.extracted or {})
        reused = _validate_extraction_dict(
            {k: v for k, v in response_payload.items() if k != "possible_duplicate"}
        )
        possible_duplicate = False
        if (
            reused is not None
            and reused.status == "ok"
            and reused.visited_on is not None
            and reused.total_krw is not None
        ):
            duplicate = await vet_repo.find_duplicate(
                session, app_user_id, draft.pet_id, reused.visited_on, reused.total_krw
            )
            possible_duplicate = duplicate is not None
        response_payload["possible_duplicate"] = possible_duplicate
    else:
        content_type = content_type_from_key(draft.receipt_image_key)
        try:
            extraction = await vet_receipt.extract(raw, content_type)
        except ReceiptExtractionFailed:
            # 우리 쪽 문제(타임아웃·API 오류). **저장하지 않는다** — 사용자가 다시 시도하면
            # 그때 또 부른다. 사진 자체는 이미 저장소에 있으므로 잃는 것이 없다.
            return DraftExtraction(
                draft=draft, status="failed", unreadable_reason=None,
                possible_duplicate=False, extraction=None,
            )

        possible_duplicate = False
        if (
            extraction.status == "ok"
            and extraction.visited_on is not None
            and extraction.total_krw is not None
        ):
            duplicate = await vet_repo.find_duplicate(
                session, app_user_id, draft.pet_id, extraction.visited_on, extraction.total_krw
            )
            possible_duplicate = duplicate is not None

        response_payload = extraction.model_dump(mode="json")
        response_payload["possible_duplicate"] = possible_duplicate

    # 응답에는 항목을 실어 보내되(response_payload, 화면은 손해를 안 본다), DB 에
    # 앉는 것(payload)은 미동의면 items 를 뺀다 — **분기는 딱 한 곳, 이 쓰기 앞에서만**
    # 이므로 이후 세 번째 경로가 생겨도 이 문을 지나야만 `draft.extracted` 에 닿는다.
    payload = dict(response_payload)
    if not consented:
        payload.pop("items", None)

    draft.extracted = payload
    draft.extracted_at = datetime.now(UTC)
    draft.receipt_sha256 = receipt_sha256
    await session.commit()

    return _draft_extraction_from_payload(draft, response_payload)


async def _owned_pet(session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID):
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise VetVisitNotFoundError
    return pet


async def reason_options(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[ReasonOption]:
    """[edit] 드롭다운의 목록 — **이 강아지가 실제로 겪은 사유가 맨 앞**, 그 뒤 전체
    목록 (docs §2 "초안 응답"). **코드와 표시명을 같이 준다** — `VET_REASON_LABELS` 가
    HTTP 경계를 안 건너면 앱이 17개 한글 표시명을 직접 하드코딩해야 하고, 그것이 닫힌
    목록으로 막으려던 드리프트다."""
    await _owned_pet(session, app_user_id, pet_id)
    visits = await vet_repo.list_between(session, app_user_id, pet_id, date.min, date.max)
    recent: list[str] = []
    for visit in visits:  # list_between 이 최근 먼저로 준다
        if visit.reason_code not in recent:
            recent.append(visit.reason_code)
    rest = [code for code in VET_REASON_CODES if code not in recent]
    return [ReasonOption(code=code, label=VET_REASON_LABELS[code]) for code in recent + rest]


async def confirm_draft(
    session: AsyncSession, app_user_id: uuid.UUID, draft_id: uuid.UUID, body: ConfirmDraftRequest
) -> VetVisit:
    """유저가 [확인] 을 누른 순간. **여기서만 `vet_visits` 에 행이 생긴다.**

    `raw_ocr_items` 는 **초안에서만** 읽는다 — `body.items` 는 받아도 무시한다.
    요청 본문의 items 를 믿으면 앱이 동의 분기를 우회하고, 클라이언트가 쓴 문자열이
    학습 코퍼스로 들어온다 (docs §3).
    """
    # vet_visits 자신의 안전망. **먼저 본다** — 이겨서 confirm 을 끝낸 요청은 초안
    # 행을 이미 지웠으므로, 재시도가 `get_draft_owned` 를 먼저 타면 초안이 없어
    # 404 가 된다. 앞의 세 층을 다 뚫고 온 재시도라도 이 키만으로 잡혀야 한다.
    existing = await vet_repo.get_by_client_event(session, app_user_id, body.client_event_id)
    if existing is not None:
        return existing

    draft = await vet_repo.get_draft_owned(session, app_user_id, draft_id)
    if draft is None:
        raise VetVisitNotFoundError

    extracted = draft.extracted or {}
    raw_ocr_items = extracted.get("items", [])
    suggested_reason_code = extracted.get("suggested_reason_code")

    visit = VetVisit(
        app_user_id=app_user_id,
        pet_id=draft.pet_id,
        visited_on=body.visited_on,
        total_krw=body.total_krw,
        hospital_name=body.hospital_name,
        hospital_address=body.hospital_address,
        hospital_phone=body.hospital_phone,
        reason_code=body.reason_code,
        reason_detail=body.reason_detail,
        suggested_reason_code=suggested_reason_code,
        is_emergency=body.is_emergency,
        is_oncology=body.is_oncology,
        raw_ocr_items=raw_ocr_items,
        receipt_image_key=draft.receipt_image_key,
        client_event_id=body.client_event_id,
    )
    vet_repo.add(session, visit)
    # 초안 행을 지운다 — **사진 객체는 안 지운다.** vet_visits 가 같은 키를 그대로
    # 물려받았으므로, 초안 청소가 나중에 이 키를 지우면 확정된 기록의 사진이 사라진다.
    await vet_repo.delete_draft(session, draft)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await vet_repo.get_by_client_event(session, app_user_id, body.client_event_id)
        if winner is None:  # pragma: no cover
            raise
        return winner
    return visit


def _window(start: date | None, end: date | None) -> tuple[date, date]:
    """조회 창. 안 보낸 쪽을 채우고 상한을 본다 (`care_event._window` 와 같은 자리)."""
    end = end or datetime.now(UTC).date()
    start = start or end - DEFAULT_RANGE
    if end < start:
        raise VetRangeError("to 는 from 보다 뒤여야 합니다.")
    if end - start > MAX_RANGE:
        raise VetRangeError(f"조회 기간은 {MAX_RANGE.days}일까지입니다.")
    return start, end


async def list_visits(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    start: date | None = None,
    end: date | None = None,
) -> tuple[list[VetVisit], date, date]:
    """기간 조회, 최근 먼저. 창을 같이 돌려주는 이유는 기본값을 앱이 되짚어 볼 수
    있게 하려는 것이다 (`care_event.list_events` 와 같은 자리)."""
    await _owned_pet(session, app_user_id, pet_id)
    start, end = _window(start, end)
    return await vet_repo.list_between(session, app_user_id, pet_id, start, end), start, end


async def delete_visit(session: AsyncSession, app_user_id: uuid.UUID, visit_id: uuid.UUID) -> None:
    """지운다. **사진 파일까지 지운다** (`screening.delete_record` 와 같은 이유) —
    확정된 기록이 사라지면 그 사진을 다른 표가 참조하지 않는다."""
    visit = await vet_repo.get_owned(session, app_user_id, visit_id)
    if visit is None:
        raise VetVisitNotFoundError
    if visit.receipt_image_key is not None:
        get_storage().delete(visit.receipt_image_key)
    await vet_repo.delete(session, visit)
    await session.commit()


__all__ = [
    "DEFAULT_RANGE",
    "DRAFT_TTL",
    "MAX_RANGE",
    "MAX_RECEIPT_BYTES",
    "SWEEP_LIMIT",
    "VET_RECEIPT_BRIDGE_DOWNLOAD_PATH",
    "VET_RECEIPT_BRIDGE_UPLOAD_PATH",
    "ConfirmDraftRequest",
    "DraftExtraction",
    "ReasonOption",
    "StartDraftRequest",
    "VetRangeError",
    "VetVisitConflictError",
    "VetVisitNotFoundError",
    "confirm_draft",
    "content_type_from_key",
    "delete_visit",
    "extract_draft",
    "list_visits",
    "reason_options",
    "start_draft",
]
