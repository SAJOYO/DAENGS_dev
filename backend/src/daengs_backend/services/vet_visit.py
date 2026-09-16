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
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from daengs_backend.core.storage import UploadTicket, build_vet_receipt_key, get_storage
from daengs_backend.models import VET_REASON_CODES, VET_REASON_LABELS, VetVisit, VetVisitDraft
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import vet_visit as vet_repo
from daengs_backend.services import vet_receipt
from daengs_backend.services.care_event import DAY_TIMEZONE
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
    """조회 창이 뒤집혔다. 라우터가 422 로 바꾼다 (`care_event.CareRangeError` 와
    같은 자리). **기간 상한으로는 안 뜬다** — 상한이 없다 (`DEFAULT_RANGE` 주석)."""


class VetSplitSumError(ValueError):
    """아이별 금액의 합이 영수증 총액과 다르다. 라우터가 422 로 바꾼다.

    **items 가 필요 없는 검사다** — 요청 안에서 닫히므로 OCR 학습 동의 여부와 무관하게
    항상 돈다. 미동의 유저의 초안에는 `items` 가 없어서(docs §3) 서버가 블록별로 더할
    재료가 없는데, 이 검산은 그 재료를 안 쓴다.
    """


class VetVisitDateError(ValueError):
    """`visited_on` 이 미래다. 라우터가 422 로 바꾼다.

    **확정을 막는 이유는 목록이 미래를 안 보여 주기 때문이다.** `_window` 의 `end` 는
    아무리 늘려도 오늘이라, 미래 날짜로 확정된 기록은 저장은 되는데 어떤 조회 창으로도
    안 잡힌다 — 유저에게는 "확인을 눌렀는데 저장소에 없다" 로 보이고, 재인식하면
    `possible_duplicate` 만 뜬다(그쪽은 창이 없다). 저장 뒤에 못 고치느니 확인 화면에서
    되돌려 주는 쪽이 싸다.
    """


#: 안 보내면 최근 1년. **상한은 없다** — 저장소의 [전체] 가 그것을 요구한다.
#: 예전에는 5년 상한이 있었는데, 그러면 "전체"를 앱이 창을 쪼개 여러 번 불러야 하고
#: 쪼개는 코드는 경계에서 한 건씩 흘린다. 상한을 없애도 되는 이유는 이 조회가
#: `(pet_id, visited_on DESC)` 인덱스를 타고 **아이 한 마리의 병원 방문**만 세기
#: 때문이다 — 연 2~6건이라 평생을 다 읽어도 수십 줄이다.
DEFAULT_RANGE = timedelta(days=366)
#: 확정이 허용하는 미래 쪽 여유. **시차 때문에 0 이 아니다** — 기기가 KST 보다 앞선
#: 시간대(최대 UTC+14)에 있으면 거기서 오늘 받은 영수증이 KST 로는 내일이다.
FUTURE_GRACE = timedelta(days=1)


def today_kst() -> date:
    """**UTC 가 아니라 KST 의 오늘이다.** `visited_on` 은 영수증에 찍힌 한국 날짜인데
    `datetime.now(UTC).date()` 는 KST 00:00~09:00 사이에 어제를 낸다. 그 9시간 동안
    오늘 찍은 영수증이 목록의 `end` 보다 뒤가 되어, 확정은 됐는데 저장소에서 사라진다
    (테스터 제보, 2026-09-13). 하루의 경계는 `care_event.DAY_TIMEZONE` 이 원본이다.
    """
    return datetime.now(ZoneInfo(DAY_TIMEZONE)).date()


@dataclass(frozen=True)
class StartDraftRequest:
    """`start_draft` 의 입력. 라우터가 pydantic 요청 본문에서 이걸 만든다."""

    pet_id: uuid.UUID
    content_type: str
    client_event_id: uuid.UUID


@dataclass(frozen=True)
class ConfirmSplit:
    """확정될 행 하나 = 아이 하나. **멱등키가 행마다 하나씩인 이유**는 `vet_visits` 의
    UNIQUE 가 `(app_user_id, client_event_id)` 라 행 단위이기 때문이다.

    `pet_id` 가 `None` 이면 초안의 강아지다 — 한 마리 확정과 구 모양 호환이 그 자리다.
    `patient_index` 는 `raw_ocr_items` 를 자르는 데만 쓰고 저장하지 않는다."""

    client_event_id: uuid.UUID
    reason_code: str
    total_krw: int
    pet_id: uuid.UUID | None = None
    reason_detail: str | None = None
    is_emergency: bool = False
    is_oncology: bool = False
    patient_index: int | None = None


@dataclass(frozen=True)
class ConfirmDraftRequest:
    """`confirm_draft` 의 입력. **`items` 를 받는 필드가 여기 없다** — `raw_ocr_items`
    는 초안에서만 읽는다 (docs §3). 여기 다시 `items` 를 더하고 싶어지면, HTTP 경계의
    `schemas.vet_visit.VetVisitConfirmRequest` 머리말이 이미 막아 둔 그 판단부터
    다시 보라는 뜻이다 — 받아도 안 쓰는 필드는 다음 편집이 집어 들 총이다.

    **`splits` 는 언제나 있고 길이가 1 이상이다.** 한 마리는 특수 케이스가 아니라
    `len(splits) == 1` 이다 — 그래야 소유권 검사도 금액 검산도 항목 자르기도 한 벌만
    존재하고, 기존 한 마리 경로가 그 한 벌을 매일 밟는다. 평평한 구 모양을 1개짜리
    리스트로 접는 일은 HTTP 경계(`schemas`)가 하고 여기까지 오지 않는다.

    `visited_on` · `hospital_*` · `total_krw` 는 **영수증 단위**다 — 아이마다 같고,
    `total_krw` 는 영수증에 찍힌 총액이라 `splits` 의 합과 대조된다."""

    visited_on: date
    total_krw: int
    splits: tuple[ConfirmSplit, ...]
    reason_detail: str | None = None
    hospital_name: str | None = None
    hospital_address: str | None = None
    hospital_phone: str | None = None


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


def _items_for(raw_items: list, split: ConfirmSplit, single: bool) -> list:
    """이 아이 몫의 항목만. **한 마리면 통째로, 나누면 블록으로 자른다.**

    `patient_index` 가 `null` 인 항목은 **나눌 때 버린다.** 블록은 있는데 어느 블록인지
    모델이 확신 못 한 항목인데, 아무 아이에게나 붙이면 학습 코퍼스에 잘못 라벨된
    데이터가 들어간다 — 코퍼스에는 빠진 것보다 **틀린 것이 나쁘다**. 금액 검산은
    항목을 안 쓰므로(`VetSplitSumError`) 버려도 아무것도 안 깨진다.
    """
    if single:
        return raw_items
    if split.patient_index is None:
        return []
    return [
        item
        for item in raw_items
        if isinstance(item, dict) and item.get("patient_index") == split.patient_index
    ]


async def confirm_draft(
    session: AsyncSession, app_user_id: uuid.UUID, draft_id: uuid.UUID, body: ConfirmDraftRequest
) -> list[VetVisit]:
    """유저가 [확인] 을 누른 순간. **여기서만 `vet_visits` 에 행이 생긴다.**

    영수증 한 장이 아이 N명이면 **행 N개**가 생긴다 (N≥1). 한 `vet_visit` 에 여러
    `pet_id` 를 매달지 않는 이유는 `reason_code` 축이 아이 하나를 전제로 서 있어서다 —
    한 방문이 5마리면 겨눈 계통이 5개가 되고, 닫힌 목록으로 지키려던 누계가 바로 그
    지점에서 깨진다 (`db/init/25_vet_visits.sql` 의 `reason_code` 주석).

    `raw_ocr_items` 는 **초안에서만** 읽는다 — `body` 가 들고 있는 값을 믿으면 앱이
    동의 분기를 우회한다 (docs §3).

    ⚠️ **초안은 여전히 정확히 한 번 지운다.** 그것이 정리가 아니라 불변식이기 때문이다 —
    `_sweep_expired` 와 `delete_visit` 의 주석이 "확정된 기록의 사진을 다른 표가 참조하지
    않는다"에 기대고 있다. 초안을 남긴 채 여러 번 확정하게 만들면, 24시간 뒤 청소가
    **확정된 기록들의 사진을 지운다.**
    """
    # 멱등 — 키별로 본다. 요청 단위 all-or-nothing 이라는 개념은 이 저장소에 없다
    # (`care_event.create` · `docs/walk/upload-idempotency.md`): 같은 키로 다른 내용이
    # 와도 먼저 온 것이 남는다. **초안보다 먼저 보는 이유**는 이겨서 확정을 끝낸 요청이
    # 초안을 이미 지웠기 때문이다 — 재시도가 초안을 먼저 타면 404 가 된다.
    keys = [split.client_event_id for split in body.splits]
    existing = await vet_repo.get_many_by_client_events(session, app_user_id, keys)
    if len(existing) == len(keys):
        return [existing[key] for key in keys]

    if body.visited_on > today_kst() + FUTURE_GRACE:
        # 멱등키 조회 **뒤**다 — 이미 확정된 재시도는 통과해야 하고, 날짜 판단은
        # 새 행을 만들 때만 한다.
        raise VetVisitDateError("영수증 날짜는 오늘보다 뒤일 수 없습니다.")

    if sum(split.total_krw for split in body.splits) != body.total_krw:
        raise VetSplitSumError("아이별 금액의 합이 영수증 총액과 다릅니다.")

    draft = await vet_repo.get_draft_owned(session, app_user_id, draft_id)
    if draft is None:
        # 초안이 이미 소진됐다. 키가 일부만 남아 있는 재시도도 여기로 온다 —
        # 새 키로 다시 확정하는 것과 구별할 방법이 없고, 구별할 이유도 없다.
        raise VetVisitNotFoundError

    extracted = draft.extracted or {}
    raw_ocr_items = extracted.get("items", [])
    single = len(body.splits) == 1
    # 제안 코드는 **영수증 하나당 하나**라 아이별 제안이 아니다. N행에 복사하면
    # `suggested_reason_code` 와 `reason_code` 의 비교가 거짓이 된다 — `label_source`
    # 칸을 안 둔 설계가 그 비교에 기대고 있어서(25_vet_visits.sql 의 주석), 한번
    # 오염되면 "기계가 제안했나 유저가 골랐나"를 영영 못 가린다. NULL 이 정확히
    # "제안이 없었다"는 뜻이므로 나눌 때는 NULL 이 사실이다.
    suggested_reason_code = extracted.get("suggested_reason_code") if single else None

    created: dict[uuid.UUID, VetVisit] = {}
    for split in body.splits:
        if split.client_event_id in existing:
            continue
        pet_id = split.pet_id or draft.pet_id
        # ⚠️ **소유권 검사가 여기 처음 생긴다.** 예전에는 `pet_id` 가 클라이언트가
        #    아니라 초안에서 와서 `start_draft` 의 검사 하나로 충분했다. splits 는
        #    앱이 `pet_id` 를 보내므로 행마다 다시 본다 — 안 보면 남의 강아지 id 로
        #    고아 행이 생기고, 외래키 위반이 멱등 복구 경로로 새어 원인이 사라진다.
        if await pet_repo.get_owned(session, app_user_id, pet_id) is None:
            raise VetVisitNotFoundError
        visit = VetVisit(
            app_user_id=app_user_id,
            pet_id=pet_id,
            visited_on=body.visited_on,
            total_krw=split.total_krw,
            hospital_name=body.hospital_name,
            hospital_address=body.hospital_address,
            hospital_phone=body.hospital_phone,
            reason_code=split.reason_code,
            reason_detail=split.reason_detail,
            suggested_reason_code=suggested_reason_code,
            is_emergency=split.is_emergency,
            is_oncology=split.is_oncology,
            raw_ocr_items=_items_for(raw_ocr_items, split, single),
            receipt_image_key=draft.receipt_image_key,
            client_event_id=split.client_event_id,
        )
        vet_repo.add(session, visit)
        created[split.client_event_id] = visit

    # 초안 행을 지운다 — **사진 객체는 안 지운다.** vet_visits 가 같은 키를 그대로
    # 물려받았으므로, 초안 청소가 나중에 이 키를 지우면 확정된 기록의 사진이 사라진다.
    # 아이 N명이면 N행이 **같은 키를 공유**한다 (`delete_visit` 이 그래서 센다).
    await vet_repo.delete_draft(session, draft)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # **이 멱등키의 충돌일 때만** 재조회로 복구한다 — 다른 UNIQUE·CHECK·외래키
        # 오류는 성공으로 바꾸지 않고 그대로 전달한다 (`docs/walk/upload-idempotency.md`).
        if not vet_repo.is_client_event_conflict(exc):
            raise
        winners = await vet_repo.get_many_by_client_events(session, app_user_id, keys)
        if len(winners) != len(keys):  # pragma: no cover — UNIQUE 가 터졌는데 행이 없을 수 없다
            raise
        return [winners[key] for key in keys]
    return [created.get(key) or existing[key] for key in keys]


def _window(start: date | None, end: date | None) -> tuple[date, date]:
    """조회 창. 안 보낸 쪽을 채운다 (`care_event._window` 와 같은 자리).

    **기간 상한은 안 본다** (`DEFAULT_RANGE` 주석) — `from=0001-01-01` 도 정당한
    요청이고, 그것이 저장소의 [전체] 다.
    """
    end = end or today_kst()
    start = start or end - DEFAULT_RANGE
    if end < start:
        raise VetRangeError("to 는 from 보다 뒤여야 합니다.")
    return start, end


async def list_visits(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    start: date | None = None,
    end: date | None = None,
) -> tuple[list[VetVisit], date, date, int]:
    """기간 조회, 최근 먼저. 창을 같이 돌려주는 이유는 기본값을 앱이 되짚어 볼 수
    있게 하려는 것이다 (`care_event.list_events` 와 같은 자리).

    **`start` 보다 오래된 기록 수도 같이 낸다.** 기본 창이 최근 1년이라 묵은 영수증은
    확정해도 이 목록에 안 뜨는데, 그 사실이 화면 어디에도 안 적혀 있으면 유저에게는
    기록이 사라진 것으로 보인다 (테스터 제보, 2026-09-13). 건수가 0 이 아닐 때만
    "언제 이후만 보입니다" 를 띄우면 평소 화면은 안 빡빡해진다.
    """
    await _owned_pet(session, app_user_id, pet_id)
    start, end = _window(start, end)
    visits = await vet_repo.list_between(session, app_user_id, pet_id, start, end)
    older = await vet_repo.count_before(session, app_user_id, pet_id, start)
    return visits, start, end, older


async def delete_visit(session: AsyncSession, app_user_id: uuid.UUID, visit_id: uuid.UUID) -> None:
    """지운다. 사진 파일도 지우되 **마지막 참조일 때만** 지운다.

    한 장에 여러 아이가 찍힌 영수증은 행 N개가 **같은 `receipt_image_key` 를 공유**한다
    (그 칸에 UNIQUE 가 없다). 예전처럼 무조건 지우면 아이 하나를 지울 때 **나머지
    아이들의 사진까지** 사라진다.

    ⚠️ **행을 지우고 커밋한 뒤에 센다.** 지우기 전에 세면 자기 자신이 세어져 영영
    0 이 안 되고, 커밋 전에 객체를 지우면 커밋이 실패했을 때 여전히 존재하는 기록의
    사진이 이미 사라진 뒤다 — `_sweep_expired` 가 커밋을 먼저 하는 것과 같은 이유다.
    """
    visit = await vet_repo.get_owned(session, app_user_id, visit_id)
    if visit is None:
        raise VetVisitNotFoundError
    storage_key = visit.receipt_image_key
    await vet_repo.delete(session, visit)
    await session.commit()
    if storage_key is None:
        return
    if await vet_repo.count_by_image_key(session, storage_key) == 0:
        get_storage().delete(storage_key)


__all__ = [
    "DEFAULT_RANGE",
    "DRAFT_TTL",
    "FUTURE_GRACE",
    "MAX_RECEIPT_BYTES",
    "SWEEP_LIMIT",
    "VET_RECEIPT_BRIDGE_DOWNLOAD_PATH",
    "VET_RECEIPT_BRIDGE_UPLOAD_PATH",
    "ConfirmDraftRequest",
    "ConfirmSplit",
    "DraftExtraction",
    "ReasonOption",
    "StartDraftRequest",
    "VetRangeError",
    "VetSplitSumError",
    "VetVisitConflictError",
    "VetVisitDateError",
    "VetVisitNotFoundError",
    "confirm_draft",
    "content_type_from_key",
    "delete_visit",
    "extract_draft",
    "list_visits",
    "reason_options",
    "start_draft",
    "today_kst",
]
