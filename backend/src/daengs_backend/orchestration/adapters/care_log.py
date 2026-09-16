"""케어 기록 쓰기 어댑터 — **이 저장소에서 유일하게 쓰는 능력** (#331 후속, D-075).

다른 어댑터와 모양은 같고 방향만 반대입니다. 얇은 것도 같습니다 — 규칙은 전부
`services/care_event.record` 에 있고(소유권 · 멱등 · 약 중복 창 · 트랜잭션 경계), 여기서 하는
일은 그 서비스의 결과와 예외를 `CapabilityResult` 로 옮기는 것뿐입니다. `/app/care-events`
POST 가 부르는 함수와 **같은 함수**라, 채팅으로 들어온 기록이 화면으로 들어온 기록과
다른 규칙을 통과하는 경로가 없습니다.

## 요청마다 새로 만듭니다

`FacilityCapabilityAdapter` 와 같은 꼴입니다 (`routers/assistant.py` 가 엔진에 실어 넣습니다).
그래야 하는 이유가 여기서는 더 분명합니다 — 이 어댑터는 `app_user_id` 를 들고 있고, 그것이
**누구 이름으로 기록되는가**입니다. 전역 어댑터로 두고 payload 에 사용자를 실으면, 그 값이
어디서 왔는지를 payload 검증이 보장하지 못합니다.

## 실패는 전부 "안 썼다" 로 나갑니다

`record` 가 던질 수 있는 것은 셋입니다. 어느 것도 예외로 새지 않고, 어느 것도 절반만 쓰지
않습니다 (`record` 가 커밋 단위):

| 무엇 | 결과 | 사용자에게 |
| --- | --- | --- |
| `MedicationConflictError` | ABSTAINED | "6시간 안에 약 기록이 있어요" + 화면으로 |
| `PetNotFoundError` | ABSTAINED | 기록 화면으로 (구성원이 아니거나 지워진 아이) |
| 그 밖의 DB 오류 | ERROR | 고정 문구 |

**약 중복은 여기서 `confirm=True` 로 밀지 않습니다.** 그 확인은 우리가 방금 받은 확인과
**다른 확인**입니다 — 사용자가 승낙한 것은 "지금 약 기록" 이고, 중복 경고는 "앞뒤 6시간에
이미 하나 있는데 그래도?" 입니다. 두 질문을 한 번의 "네" 로 뭉개면, 교대 근무에서 두 사람이
같은 약을 먹이는 것을 막으려고 만든 창이 채팅에서만 무력해집니다 (`docs/co-care.md` §4).
그 판단에는 충돌 목록을 보여 줘야 하고, 그것을 그리는 곳은 기록 화면입니다.
"""

from __future__ import annotations

import logging
import time
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from daengs_backend.orchestration.care_log import KIND_LABELS
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    CareLogProposal,
    ErrorDetail,
    OutcomeDetail,
)
from daengs_backend.schemas.care_event import CareEventCreate
from daengs_backend.services import care_event as care_service
from daengs_backend.services.pet import PetNotFoundError

log = logging.getLogger(__name__)

__all__ = ["CareLogCapabilityAdapter"]

_SCREEN_HINT = "기록 화면에서 남겨 주세요."


class CareLogCapabilityAdapter:
    capability = CapabilityName.CARE_LOG

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        app_user_id: uuid.UUID,
    ) -> None:
        self._session_factory = session_factory
        self._app_user_id = app_user_id

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        """payload 를 그대로 기록한다. **고르는 것이 하나도 없다.**

        `payload` 는 사용자가 지난 턴에 문장으로 보고 승낙한 제안이고
        (`planner.resolve_care_log_write` 가 옮겨만 왔다), 여기서 종류·시각·강아지·멱등키 중
        어느 것도 다시 정하지 않는다.
        """
        started = time.perf_counter()
        proposal = request.payload
        if not isinstance(proposal, CareLogProposal):  # pragma: no cover - 계약이 이미 막는다
            raise TypeError("care_log requires CareLogProposal")

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1_000)

        body = _create_body(proposal)
        try:
            async with self._session_factory() as session:
                event, created = await care_service.record(session, self._app_user_id, body)
        except care_service.MedicationConflictError as exc:
            # 몸에 닿는 경고다 — 밀지 않는다 (모듈 머리말).
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.ABSTAINED,
                abstention=OutcomeDetail(
                    code="care_log.medication_conflict",
                    message=(
                        f"앞뒤 6시간 안에 약 기록이 {len(exc.conflicts)}건 있어요. "
                        f"그래도 남기시려면 {_SCREEN_HINT}"
                    ),
                ),
                elapsed_ms=elapsed(),
            )
        except PetNotFoundError:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.ABSTAINED,
                abstention=OutcomeDetail(
                    code="care_log.pet_not_accessible",
                    message=f"이 아이의 기록을 남길 수 없어요. {_SCREEN_HINT}",
                ),
                elapsed_ms=elapsed(),
            )
        except SQLAlchemyError as exc:
            # `care_events` 표가 아직 없는 서버도 여기로 온다 (`services/care_log_context`
            # 가 읽기에서 같은 것을 다루는 자리와 같은 이유). 원문은 로그에만 남긴다.
            log.warning("케어 기록을 남기지 못했습니다 request_id=%s: %s", request_id, exc)
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.ERROR,
                error=ErrorDetail(
                    kind="care_log_write_failed",
                    detail=f"기록을 남기지 못했어요. {_SCREEN_HINT}",
                ),
                elapsed_ms=elapsed(),
            )

        seoul = ZoneInfo(care_service.DAY_TIMEZONE)
        clock = event.occurred_at.astimezone(seoul).strftime("%H:%M")
        label = KIND_LABELS[proposal.kind]
        # `created=False` 는 같은 제안에 두 번 승낙한 것이다 (`proposal_id` 가 멱등키).
        # 두 번째에도 같은 문장을 내는 것이 의도다 — "이미 기록했어요" 로 갈라 봤자
        # 사용자가 할 일이 없고, 첫 응답을 못 본 재시도와 두 번 말한 것을 우리가 못 가른다.
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={
                "answer": f"{clock}에 {label} 기록했어요.",
                "care_event_id": str(event.id),
                "kind": proposal.kind.value,
                "occurred_at": event.occurred_at.isoformat(),
                "created": created,
            },
            elapsed_ms=elapsed(),
        )


def _create_body(proposal: CareLogProposal) -> CareEventCreate:
    """제안 → `/app/care-events` 가 받는 것과 **같은** 본문.

    `confirm` 은 기본값 `False` 로 둔다 — 약 중복 창을 채팅이 건너뛰지 않는다는 뜻이고,
    그 이유는 모듈 머리말에 있다.
    """
    return CareEventCreate(
        pet_id=proposal.pet_id,
        kind=proposal.kind.value,
        occurred_at=proposal.occurred_at,
        note=None,
        client_event_id=proposal.proposal_id,
    )
