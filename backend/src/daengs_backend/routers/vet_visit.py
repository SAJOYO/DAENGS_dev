"""진료비 기록 HTTP 경계 (`/app/vet-visits`) — 서비스의 판단을 상태 코드로 바꾼다 (#353).

판단은 여기 없다. "동의했나"·"같은 영수증인가"·"이 강아지가 내 것인가"·
"언제 확정 행이 생기는가" 는 전부 `services/vet_visit.py` 가 정한다.

**경로가 `/app/` 아래인 이유**는 앱 회원 전용이기 때문이다 (`/app/care-events`·
`/app/walks` 와 같은 규칙). 여섯 개 엔드포인트는 각자 `CurrentAppUser` 로 잠겨 있다.

⚠️ **예외가 둘 있다 — `/_bridge/upload`·`/_bridge/download`.** 이 둘은 인증 헤더를
안 받는다(Signed URL 을 흉내 내는 자리, `screening.py` 와 같은 규칙). 대신 **키 자체가
자격이다** — 추측 불가능한 uuid 가 들어 있고, backend 가 실제로 발급한 키인지를
`vet_repo.find_draft_by_image_key` 로 DB 에 대조하며, 업로드는 `exclusive=True` 라
그 키로 한 번만 쓸 수 있다. 그래서 인증 없이도 안전하다 — 자세한 근거는 두 함수
바로 위 주석.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.models import VET_REASON_CODES, VET_REASON_LABELS, VetVisit, VetVisitDraft
from daengs_backend.repositories import vet_visit as vet_repo
from daengs_backend.schemas.vet_visit import (
    VetVisitConfirmRequest,
    VetVisitDraftResponse,
    VetVisitListResponse,
    VetVisitReasonOptionOut,
    VetVisitReceiptItemOut,
    VetVisitResponse,
    VetVisitStartRequest,
    VetVisitTicketResponse,
)
from daengs_backend.services import vet_visit as vet_service

router = APIRouter(prefix="/app/vet-visits", tags=["vet-visits"])

Session = Annotated[AsyncSession, Depends(get_session)]

_PET_NOT_FOUND = "강아지를 찾을 수 없습니다."
_VISIT_NOT_FOUND = "기록을 찾을 수 없습니다."


def _receipt_image_url(draft: VetVisitDraft) -> str | None:
    """확인 화면이 사진을 보여줄 주소. 초안은 사진 없이는 존재하지 않아 항상 있다.

    만료(`gait_download_url_ttl_seconds`)는 도메인이 달라도 같은 값을 쓴다 —
    `screening.get_record` 가 이미 같은 값을 재사용한다.
    """
    return vet_service.get_storage().download_url(
        draft.receipt_image_key,
        expires_in_seconds=settings.gait_download_url_ttl_seconds,
        bridge_download_path=vet_service.VET_RECEIPT_BRIDGE_DOWNLOAD_PATH,
    )


def _reason_option_out(option: vet_service.ReasonOption) -> VetVisitReasonOptionOut:
    return VetVisitReasonOptionOut(code=option.code, label=option.label)


def _to_draft_response(
    result: vet_service.DraftExtraction, reason_options: list[vet_service.ReasonOption]
) -> VetVisitDraftResponse:
    extraction = result.extraction
    return VetVisitDraftResponse(
        draft_id=result.draft.id,
        pet_id=result.draft.pet_id,
        receipt_image_url=_receipt_image_url(result.draft),
        extraction_status=result.status,
        unreadable_reason=result.unreadable_reason,
        visited_on=extraction.visited_on if extraction else None,
        total_krw=extraction.total_krw if extraction else None,
        hospital_name=extraction.hospital_name if extraction else None,
        hospital_address=extraction.hospital_address if extraction else None,
        hospital_phone=extraction.hospital_phone if extraction else None,
        items=(
            [
                VetVisitReceiptItemOut(
                    name=i.name, amount_krw=i.amount_krw, patient_index=i.patient_index
                )
                for i in extraction.items
            ]
            if extraction
            else []
        ),
        patient_count=extraction.patient_count if extraction else 1,
        suggested_reason_code=extraction.suggested_reason_code if extraction else None,
        is_emergency=extraction.is_emergency if extraction else False,
        possible_duplicate=result.possible_duplicate,
        reason_options=[_reason_option_out(o) for o in reason_options],
    )


def _to_response(visit: VetVisit) -> VetVisitResponse:
    return VetVisitResponse(
        id=visit.id,
        pet_id=visit.pet_id,
        visited_on=visit.visited_on,
        total_krw=visit.total_krw,
        hospital_name=visit.hospital_name,
        hospital_address=visit.hospital_address,
        hospital_phone=visit.hospital_phone,
        reason_code=visit.reason_code,
        reason_detail=visit.reason_detail,
        suggested_reason_code=visit.suggested_reason_code,
        is_emergency=visit.is_emergency,
        is_oncology=visit.is_oncology,
        client_event_id=visit.client_event_id,
        created_at=visit.created_at,
    )


@router.post("", response_model=VetVisitTicketResponse, status_code=status.HTTP_201_CREATED)
async def start_draft(
    body: VetVisitStartRequest,
    response: Response,
    user: CurrentAppUser,
    session: Session,
) -> VetVisitTicketResponse:
    """진료비 영수증 초안 한 줄을 열고 사진 올릴 자리를 발급한다.

    새로 만들었으면 201, 같은 `client_event_id` 가 이미 있으면 200 과 함께 있던
    초안의 티켓을 그대로 돌려준다 — 앱은 둘 다 "올라갔다" 로 본다
    (`/app/care-events`·`/app/walks` 와 같은 규칙). 사진도 Gemini 도 다시 안 간다.
    """
    req = vet_service.StartDraftRequest(
        pet_id=body.pet_id, content_type=body.content_type, client_event_id=body.client_event_id
    )
    try:
        draft, ticket, created = await vet_service.start_draft(session, user.app_user_id, req)
    except vet_service.VetVisitNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return VetVisitTicketResponse(
        draft_id=draft.id,
        pet_id=draft.pet_id,
        storage_key=ticket.storage_key,
        upload_url=ticket.upload_url,
        upload_headers=ticket.headers,
        expires_in_seconds=ticket.expires_in_seconds,
    )


# ⚠️ `/{draft_id}` 보다 먼저 선언한다. `/app/pets/primary` 가 그 순서 때문에 422 를
#    낸 적이 있어 같은 규칙을 지킨다 (`care_event.py` 의 `/today` 와 같은 이유).
@router.get("/reason-options", response_model=list[VetVisitReasonOptionOut])
async def get_reason_options(
    user: CurrentAppUser,
    session: Session,
    pet_id: Annotated[uuid.UUID, Query()],
) -> list[VetVisitReasonOptionOut]:
    """[edit] 드롭다운의 목록. 이 강아지가 실제로 겪은 사유가 맨 앞이다.

    코드만이 아니라 표시명도 같이 낸다 — 앱이 17개 한글 표시명을 하드코딩하면
    닫힌 목록을 서버가 지키는 이유(docs §1)가 그 자리에서 다시 샌다.
    """
    try:
        options = await vet_service.reason_options(session, user.app_user_id, pet_id)
    except vet_service.VetVisitNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    return [_reason_option_out(o) for o in options]


@router.get("", response_model=VetVisitListResponse)
async def list_visits(
    user: CurrentAppUser,
    session: Session,
    pet_id: Annotated[uuid.UUID, Query()],
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> VetVisitListResponse:
    """기간 조회, 최근 먼저. 안 보내면 최근 1년이고, **기간 상한은 없다** —
    `from=0001-01-01` 이 저장소의 [전체] 다.

    **`from`/`to` 로 더 옛날도 볼 수 있다** — 기본 창이 감춘 건수가 `older_count` 로
    같이 오므로, 앱은 그 값이 0 이 아닐 때만 "언제 이후만 보입니다" 를 띄우면 된다.

    내 강아지가 아니면 404 다.
    """
    try:
        visits, start_, end_, older = await vet_service.list_visits(
            session, user.app_user_id, pet_id, start=start, end=end
        )
    except vet_service.VetVisitNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except vet_service.VetRangeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return VetVisitListResponse(
        pet_id=pet_id,
        start=start_,
        end=end_,
        visits=[_to_response(v) for v in visits],
        older_count=older,
    )


@router.post("/{draft_id}/extract", response_model=VetVisitDraftResponse)
async def extract_draft(
    draft_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> VetVisitDraftResponse:
    """올라온 영수증 사진을 읽는다. **`ok`·`unreadable`·`failed` 모두 200 이다** —
    셋 다 에러가 아니다(docs/vet-visits.md §2 "못 읽었을 때"). 유저는 셋 모두에서
    손으로 채워 확정할 수 있다. 내 초안이 아니면 404 다.

    ⚠️ **미동의 상태로 이미 추출된 초안을 다시(멱등 ③) 부르면, 이 응답에도
    `items` 가 없다.** 저장된 `draft.extracted` 를 그대로 돌려주는데, 미동의 경로는
    그 칸에 `items` 를 저장 자체를 안 하기 때문이다(docs §3) — 없는 것을 재추출로
    되살릴 수 없다. 앱은 이 화면을 다시 불러 채우는 것에 기대면 안 된다. 최초 호출
    (동의 여부와 무관)에서는 응답에 항상 `items` 가 실려 있다 — 화면은 그 순간에는
    손해를 안 본다.
    """
    try:
        result = await vet_service.extract_draft(session, user.app_user_id, draft_id)
    except vet_service.VetVisitNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _VISIT_NOT_FOUND) from None
    except vet_service.VetVisitConflictError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": exc.code, "message": exc.detail}
        ) from None
    try:
        reason_opts = await vet_service.reason_options(
            session, user.app_user_id, result.draft.pet_id
        )
    except vet_service.VetVisitNotFoundError:  # pragma: no cover — 초안이 있으면 강아지도 있다
        reason_opts = [
            vet_service.ReasonOption(code=code, label=VET_REASON_LABELS[code])
            for code in VET_REASON_CODES
        ]
    return _to_draft_response(result, reason_opts)


@router.post("/{draft_id}/confirm", response_model=VetVisitResponse | list[VetVisitResponse])
async def confirm_draft(
    draft_id: uuid.UUID,
    body: VetVisitConfirmRequest,
    user: CurrentAppUser,
    session: Session,
) -> VetVisitResponse | list[VetVisitResponse]:
    """유저가 [확인] 을 누른 순간. **`vet_visits` 에 행이 생기는 유일한 자리다.**

    항목(`raw_ocr_items`)은 이 본문에 없다 — 초안에서만 읽는다(docs §3). 내 초안이
    아니면 404 다. **미래 날짜는 422 다** — 목록의 `end` 는 늘 오늘이라, 미래로
    확정된 기록은 저장은 되는데 어떤 창으로도 안 잡힌다 (`VetVisitDateError`).

    영수증 한 장에 아이가 여럿이면 **행이 여럿 생긴다** — `splits` 마다 하나씩이고,
    아이별 금액의 합이 영수증 총액과 다르면 422 다 (`VetSplitSumError`). 응답은
    요청 모양을 따라간다: 구 모양(평평한 본문)이면 단일 객체, `splits` 면 배열.
    그 분기는 한시적이다 — `schemas.VetVisitConfirmRequest` 머리말 참고.
    """
    req = vet_service.ConfirmDraftRequest(
        visited_on=body.visited_on,
        total_krw=body.total_krw,
        hospital_name=body.hospital_name,
        hospital_address=body.hospital_address,
        hospital_phone=body.hospital_phone,
        splits=tuple(
            vet_service.ConfirmSplit(
                client_event_id=split.client_event_id,
                pet_id=split.pet_id,
                reason_code=split.reason_code,
                reason_detail=split.reason_detail,
                total_krw=split.total_krw,
                is_emergency=split.is_emergency,
                is_oncology=split.is_oncology,
                patient_index=split.patient_index,
            )
            for split in body.splits
        ),
    )
    try:
        visits = await vet_service.confirm_draft(session, user.app_user_id, draft_id, req)
    except vet_service.VetVisitNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _VISIT_NOT_FOUND) from None
    except (vet_service.VetVisitDateError, vet_service.VetSplitSumError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    # ⚠️ **한시적 분기다.** 구 모양으로 보낸 앱은 단일 객체를 기대한다 — 배열을 주면
    #    그 자리에서 깨진다. 앱이 새 버전으로 깔리면 이 두 줄과 `schemas` 의
    #    `_fold_legacy_shape` · `_LEGACY_SPLIT_FIELDS` 가 **한꺼번에** 지워지고,
    #    그때부터 이 엔드포인트는 언제나 리스트를 낸다.
    if body._from_legacy_shape:
        return _to_response(visits[0])
    return [_to_response(visit) for visit in visits]


@router.delete("/{visit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_visit(
    visit_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> None:
    """지운다. 내 기록이 아니면 404 다 — 남의 것도 같은 404 라 존재 여부가 안 샌다."""
    try:
        await vet_service.delete_visit(session, user.app_user_id, visit_id)
    except vet_service.VetVisitNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _VISIT_NOT_FOUND) from None


# ── local 저장소의 bridge (`screening.py` 의 `_bridge_upload`·`_bridge_download` 와
#    같은 자리 규칙) ─────────────────────────────────────────────────────
#
# ⚠️ **인증 헤더를 요구하지 않습니다 — 대신 키가 자격입니다.** Signed URL 을 흉내 내는
#    자리라, 헤더를 요구하면 저장소를 GCS 로 되돌릴 때 앱 코드가 또 바뀝니다. 대신
#    **backend 가 실제로 발급한 키인지**를 DB 로 확인합니다(`vet_repo.
#    find_draft_by_image_key`). 키에 draft_id(uuid)가 들어 있어 추측이 안 되는 것이
#    나머지 절반입니다.


def _local_bridge():
    from daengs_backend.core.storage import LocalBridgeStorage

    storage = vet_service.get_storage()
    if not isinstance(storage, LocalBridgeStorage):
        # gcs/none 모드에서는 이 경로가 없는 것처럼 404.
        raise HTTPException(status.HTTP_404_NOT_FOUND, _VISIT_NOT_FOUND)
    return storage


@router.put("/_bridge/upload/{storage_key:path}", include_in_schema=False)
async def _bridge_upload(session: Session, storage_key: str, request: Request) -> Response:
    """발급된 초안 하나에만 영수증 사진을 받습니다. 디스크로 흘려 씁니다.

    **바이트를 메모리에 안 올립니다** — `core/storage.py` 머리말이 말하는 대로 모든
    바이트가 backend 를 지나므로(D-052), 통째로 읽으면 사진 하나가 그대로 메모리다.
    """
    storage = _local_bridge()
    draft = await vet_repo.find_draft_by_image_key(session, storage_key)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _VISIT_NOT_FOUND)

    expected_type = vet_service.content_type_from_key(draft.receipt_image_key)
    declared_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if declared_type != expected_type:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "발급된 사진 형식과 Content-Type 이 다릅니다.",
        )

    # 영수증 하나의 상한 — 추출이 읽는 값과 같은 숫자다(services.vet_visit.
    # MAX_RECEIPT_BYTES). 여기서 따로 정하면 둘이 어긋나는 날이 온다.
    limit = vet_service.MAX_RECEIPT_BYTES
    too_large = HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        f"사진은 {limit // (1024 * 1024)} MiB 이하여야 합니다.",
    )

    # 큰 파일을 다 받고 나서 거절하면 대역폭과 디스크를 이미 쓴 뒤입니다.
    # Content-Length 는 앱이 주는 값이라 **믿지 않고**, 아래 누적 검사로 다시 봅니다.
    declared_size = request.headers.get("content-length")
    if declared_size is not None:
        try:
            if int(declared_size) > limit:
                raise too_large
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Content-Length 가 올바르지 않습니다."
            ) from None

    # exclusive=True 는 create-only 입니다. 도중에 끊기면 open_write 가 반쯤 쓴
    # 파일을 지웁니다 — 남으면 다음 PUT 이 409 에 막히고, 0바이트로 남으면
    # 다른 tombstone 과 구별되지 않습니다.
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
                    status.HTTP_400_BAD_REQUEST, "빈 사진은 업로드할 수 없습니다."
                )
    except FileExistsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이미 올린 사진은 같은 티켓으로 덮어쓸 수 없습니다.",
        ) from None
    return Response(status_code=status.HTTP_200_OK)


@router.get("/_bridge/download/{storage_key:path}", include_in_schema=False)
async def _bridge_download(session: Session, storage_key: str):
    """초안의 영수증 사진을 내려줍니다. 확정되면 초안 행이 지워지므로(services.
    confirm_draft) 확정 뒤에는 이 경로도 자연히 404 입니다."""
    from fastapi.responses import FileResponse

    storage = _local_bridge()
    draft = await vet_repo.find_draft_by_image_key(session, storage_key)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _VISIT_NOT_FOUND)
    path = storage.local_path(storage_key)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, _VISIT_NOT_FOUND)
    return FileResponse(path, media_type=vet_service.content_type_from_key(storage_key))


__all__ = ["router"]
