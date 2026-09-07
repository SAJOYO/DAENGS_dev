"""판정 기록 한 건 → 오케스트레이션이 받아도 되는 사실 둘 (#307).

`services/dog_context.py` 와 같은 층이고 같은 규칙입니다 — **소유권을 확인해 읽고, 좁혀서
넘기고, 못 채우면 조용히 None**. 넘어가는 모양의 정의는 `orchestration.contracts`
의 `ScreeningContext` 이고, 이 파일은 **그 타입을 실제로 통과시켜서** 좁힘을 강제합니다:
나중에 누가 분포나 문구를 얹으려 하면 여기서 `extra="forbid"` 에 걸립니다.

**앱이 결과 본문을 보내지 않는 이유가 이 파일의 존재 이유입니다.** `/assistant/query` 의
응답은 대화 turn 으로 **저장**되므로(D-048, `services/chat.py public_response_of`),
검증하지 않은 판정이 한 번 들어가면 지난 turn 에서 되돌릴 수 없습니다. 그래서 앱은
`screening_record_id` 만 보내고 판정 내용은 서버가 DB 에서 읽습니다.

**여기서 실행하는 것은 없습니다.** Skin 은 여전히 HANDOFF 전용이고
(`docs/orchestration/routing.md` §5 인가 매트릭스), 이 파일이 읽는 것은 **이미 끝난
판정의 기록**입니다. `CapabilityName` 에 `skin` 을 더하지 않습니다.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import ScreeningRecord
from daengs_backend.orchestration.contracts import ScreeningContext
from daengs_backend.repositories import screening as screening_repo

__all__ = ["resolve"]

#: 판정이 끝난 기록만 씁니다. `PENDING_UPLOAD` 는 사진도 안 올라온 자리이고, `FAILED` 는
#: `failure_reason` 이 운영자용이라 사용자 경로로 새면 안 됩니다 (`routers/screening.py`).
_USABLE_STATUS = "DONE"

#: `agent.contract()` 가 내는 세 값. 계약이 늘면 **여기서 막히는 것이 맞습니다** —
#: 모르는 판정을 아는 척 넘기면 하류가 그것을 해석해 버립니다.
_VERDICTS = frozenset({"normal", "abnormal", "retake"})

#: `ScreeningContext.days_ago` 의 상한과 같은 값. 10년보다 오래된 기록은 여기서 잘립니다 —
#: "아주 오래됐다" 를 넘는 정밀도가 답을 가르지 않아서, 자르는 편이 계약을 넓히는 것보다 쌉니다.
_MAX_DAYS = 3_650


async def resolve(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    screening_record_id: uuid.UUID | str,
    *,
    now: datetime.datetime | None = None,
) -> dict[str, object] | None:
    """`context["screening"]` 에 넣을 값. **없으면 None 이고, 그것은 오류가 아닙니다.**

    남의 기록 · 없는 기록 · 아직 판정 전 · 실패한 기록 · 모양이 낡은 기록이 전부 None 으로
    옵니다. 여기서 4xx 를 내면 **기록을 못 찾았다는 이유로 답할 수 있는 질문이 실패합니다** —
    `dog_context.resolve` 와 같은 판단이고, 앱이 옛 `/screen/v1/screen` fallback 으로 찍은
    건은 애초에 행이 없어서 이 자리가 상시로 열려 있어야 합니다 (#239 컨텍스트).

    소유권은 `screening_repo.get_owned` 가 쿼리 조건으로 묶습니다 — 남의 id 를 넣어도
    못 읽고, 그래서 "없음" 과 "남의 것" 이 여기서 이미 같은 답입니다.
    """
    try:
        record_id = uuid.UUID(str(screening_record_id))
    except (ValueError, AttributeError, TypeError):
        return None
    record = await screening_repo.get_owned(session, app_user_id, record_id)
    if record is None:
        return None
    verdict = _verdict(record)
    if verdict is None:
        return None
    days = _days_ago(record, now=now)
    if days is None:
        return None
    return ScreeningContext(verdict=verdict, days_ago=days).model_dump()


def _verdict(record: ScreeningRecord) -> str | None:
    """`result` 에서 판정 **한 칸만** 꺼냅니다.

    `result` 는 통째로 저장된 모델 계약이라(`models/screening_record.py`) 분포도 문구도
    같이 들어 있습니다. 그것을 여기서 지나가게 두면 `ScreeningContext` 의 좁힘이 무의미해지므로
    키를 하나만 읽습니다 — `dict` 를 넘겨 받아 걸러내는 것이 아니라, 애초에 하나만 꺼냅니다.
    """
    if record.status != _USABLE_STATUS:
        return None
    result = record.result
    if not isinstance(result, dict):
        return None
    verdict = result.get("verdict")
    if not isinstance(verdict, str) or verdict not in _VERDICTS:
        return None
    return verdict


def _days_ago(record: ScreeningRecord, *, now: datetime.datetime | None) -> int | None:
    """언제 찍은 것인지 모르면 **아예 안 넘깁니다.**

    경과를 모르는 기록은 "지난번" 을 말할 수 없고, 0 으로 채우면 방금 찍은 것처럼 읽힙니다.
    `created_at` 은 서버 기본값이라 실제로는 늘 있지만, 없을 때 지어내지 않는 쪽이 맞습니다.

    앞날짜(시계 어긋남)는 0 으로 봅니다 — 음수 경과를 만들지 않습니다.
    """
    created = record.created_at
    if created is None:
        return None
    reference = now or datetime.datetime.now(datetime.UTC)
    if created.tzinfo is None:
        # `DateTime(timezone=True)` 컬럼이지만 드라이버·테스트 경로에 따라 naive 로 옵니다.
        created = created.replace(tzinfo=datetime.UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=datetime.UTC)
    days = (reference - created).days
    return max(0, min(days, _MAX_DAYS))
