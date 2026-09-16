"""판정 기록 한 건 → 오케스트레이션이 받아도 되는 사실 둘 (#307).

`services/dog_context.py` 와 같은 층이고 같은 규칙입니다 — **소유권을 확인해 읽고, 좁혀서
넘기고, 못 채우면 조용히 None**. 넘어가는 모양의 정의는 `orchestration.contracts`
의 `ScreeningContext` 이고, 이 파일은 **그 타입을 실제로 통과시켜서** 좁힘을 강제합니다:
나중에 누가 분포나 문구를 얹으려 하면 여기서 `extra="forbid"` 에 걸립니다.

**앱이 결과 본문을 보내지 않는 이유가 이 파일의 존재 이유입니다.** `/assistant/query` 의
응답은 대화 turn 으로 **저장**되므로(D-048, `services/chat.py public_response_of`),
검증하지 않은 판정이 한 번 들어가면 지난 turn 에서 되돌릴 수 없습니다. 그래서 앱은
`screening_record_id` 만 보내고 판정 내용은 서버가 DB 에서 읽습니다.

**여기서 실행하는 것은 없습니다.** 이 파일이 읽는 것은 **이미 끝난 판정의 기록**이고,
판정을 새로 내는 Skin 은 여전히 HANDOFF 입니다 (`docs/orchestration/routing.md` §5).
이 기록을 **해설**하는 `skin` 능력(D-079, `orchestration/adapters/skin.py`)이 이 값을
읽지만, 그쪽도 여기서 좁힌 두 사실 말고는 받지 않습니다.

**이력도 같은 자리입니다** (#79 3번). "지난번보다 어때요" 에 답하려면 기록이 여러 건
필요한데, 이력에는 앱이 보낼 참조가 없습니다 — 그래서 **`screening_record_id` 가 온
요청에서만** 그 기록의 아이로 이전 기록을 읽습니다. 결과 화면에서 이어 묻는 자리가
곧 이력이 쓸모 있는 자리라, 진입 신호를 새로 만들지 않고 이미 있는 참조에 얹습니다.
일반 대화 전반의 피부 기억(`active_dog_id` 만으로 상시 읽기)은 실제 수요가 확인된 뒤에
넓힙니다 — 넓히는 것은 `resolve_context` 의 조건 한 줄입니다.

⚠️ **여기서 "나아졌다 / 진행됐다" 를 계산하지 않습니다.** 두 시점의 차이는 강아지의
변화가 아니라 모델의 잡음일 수 있습니다 (D-023 — 2단계 병변명 holdout 오답 56.6%,
`stage1` 은 보정 전). 이 층이 내는 것은 "이전 기록이 있고 그때는 이런 판정이었다"
까지이고, 비교할 데이터를 계약에 안 두는 것이 그 방어입니다.

**판정은 `_accessible` — 구성원(대표 ∪ 돌보미)입니다** (Task 14, docs/co-care.md §2).
`dog_context.py` 가 먼저 구성원으로 열렸는데 여기만 창작자로 남으면, 돌보미가 목록에서
자기가 본 기록(`GET /app/screening/records/{id}` 로 이미 볼 수 있는 것)을 짚어 물어도
어시스턴트 컨텍스트만 조용히 비어 — Task 12 가 생성을 닫았던 "쪼개진 이력" 모양이 대화
쪽에서 되풀이됩니다. 넓혀도 **새로 보이는 것은 없습니다** — 여기 넘어오는 `app_user_id`
는 언제나 **지금 묻는 사람 자신**이라, `get_accessible`/`list_accessible` 은 그 사람이
`/app/screening/*` 로 이미 읽을 수 있는 것과 정확히 같은 집합을 돌려줍니다. 개인 기록
(`pet_id IS NULL`)은 그 서브쿼리 바깥이라 여기서도 그대로 창작자만입니다.

`confirm_record`(판정 상태를 실제로 바꾸는 자리)는 이 파일이 안 씁니다 — 거긴 여전히
`screening_repo.get_owned` 로 창작자만입니다. 이 파일이 넓힌 것은 **읽기**뿐입니다.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import ScreeningRecord
from daengs_backend.orchestration.contracts import (
    SCREENING_HISTORY_LIMIT,
    ScreeningContext,
    ScreeningHistory,
)
from daengs_backend.repositories import screening as screening_repo

__all__ = ["resolve", "resolve_context"]

#: 판정이 끝난 기록만 씁니다. `PENDING_UPLOAD` 는 사진도 안 올라온 자리이고, `FAILED` 는
#: `failure_reason` 이 운영자용이라 사용자 경로로 새면 안 됩니다 (`routers/screening.py`).
_USABLE_STATUS = "DONE"

#: `agent.contract()` 가 내는 세 값. 계약이 늘면 **여기서 막히는 것이 맞습니다** —
#: 모르는 판정을 아는 척 넘기면 하류가 그것을 해석해 버립니다.
_VERDICTS = frozenset({"normal", "abnormal", "retake"})

#: `ScreeningContext.days_ago` 의 상한과 같은 값. 10년보다 오래된 기록은 여기서 잘립니다 —
#: "아주 오래됐다" 를 넘는 정밀도가 답을 가르지 않아서, 자르는 편이 계약을 넓히는 것보다 쌉니다.
_MAX_DAYS = 3_650

#: 이력을 만들려고 실제로 읽는 행 수. `SCREENING_HISTORY_LIMIT` 보다 넉넉한 이유는 여기서
#: **기준 기록 자신과 `DONE` 이 아닌 행을 건너뛰기** 때문입니다 — 딱 3개만 읽으면 실패가
#: 몇 번 낀 아이의 이력이 통째로 비어 보입니다. 그렇다고 무제한으로 읽으면 오래 쓴 아이일수록
#: 한 요청이 비싸지므로, 훑는 쪽에도 상한을 둡니다.
_HISTORY_SCAN_LIMIT = 12


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

    접근은 `_accessible` 가 봅니다 — "없음" 과 "못 보는 것" 이 거기서 이미 같은 답입니다.

    **이력은 여기 없습니다.** 부르는 쪽이 둘 다 필요하면 `resolve_context` 를 부르세요 —
    기록을 두 번 읽지 않으려고 그쪽이 한 함수입니다.
    """
    record = await _accessible(session, app_user_id, screening_record_id)
    if record is None:
        return None
    return _narrowed(record, now=now)


async def resolve_context(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    screening_record_id: uuid.UUID | str,
    *,
    now: datetime.datetime | None = None,
) -> dict[str, object]:
    """`context` 에 **합칠 조각들**. 아무것도 못 채우면 `{}` 이고, 그것은 오류가 아닙니다.

    `screening` 은 지목된 기록 한 건이고(#307), `screening_history` 는 **같은 아이의 이전
    기록**입니다 (#79 3번). 한 함수인 이유는 **기록을 한 번만 읽으려고**서입니다 — 이력의
    아이는 그 기록에서 오므로, 부르는 쪽이 `resolve` 와 따로 부르면 같은 행을 두 번 읽습니다.

    이력은 `screening_record_id` 가 온 요청에서만 읽습니다. 그 요청은 이미 세션을 열고
    조회를 하나 내므로 **늘어나는 것은 같은 세션의 쿼리 하나**이고, 그 필드를 안 보내는
    요청은 이 함수를 아예 안 지납니다 (`routers/assistant.py`).

    ⚠️ **기준 기록이 못 쓸 것이어도 이력은 읽습니다.** 방금 찍은 판정이 `FAILED` 인 자리에서
    "지난번엔 어땠지" 를 묻는 것이 그대로 유효한 질문이라, `screening` 만 비고 이력은 갑니다.
    거꾸로 **기록을 못 찾으면 이력도 없습니다** — 아이를 알 방법이 그 기록뿐입니다.
    """
    record = await _accessible(session, app_user_id, screening_record_id)
    if record is None:
        return {}
    context: dict[str, object] = {}
    current = _narrowed(record, now=now)
    if current is not None:
        context["screening"] = current
    history = await _history(session, app_user_id, record, now=now)
    if history:
        context["screening_history"] = history
    return context


async def _accessible(
    session: AsyncSession, app_user_id: uuid.UUID, screening_record_id: uuid.UUID | str
) -> ScreeningRecord | None:
    """내가 볼 수 있는 기록 한 건 — 창작자이거나, 강아지에 붙었고 그 아이의 구성원.
    모양이 틀린 id 도 **없는 기록과 같은 답**입니다.

    `screening_repo.get_accessible` 이 쿼리 조건으로 묶습니다 — 남의 id 를 넣어도
    못 읽고, 그래서 "없음" 과 "남의 것" 이 여기서 이미 같습니다. 개인 기록
    (`pet_id IS NULL`)은 그 함수의 구성원 서브쿼리 바깥이라 여전히 창작자만입니다.
    """
    try:
        record_id = uuid.UUID(str(screening_record_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return await screening_repo.get_accessible(session, app_user_id, record_id)


def _narrowed(
    record: ScreeningRecord, *, now: datetime.datetime | None
) -> dict[str, object] | None:
    """행 하나 → `ScreeningContext` 한 벌. **계약을 실제로 통과시킵니다.**

    `dict` 를 손으로 짜서 넘기지 않는 이유는 좁힘을 강제하는 것이 이 통과 자체이기
    때문입니다 — 나중에 누가 분포나 문구를 얹으면 여기서 `extra="forbid"` 에 걸립니다.
    """
    verdict = _verdict(record)
    if verdict is None:
        return None
    days = _days_ago(record, now=now)
    if days is None:
        return None
    return ScreeningContext(verdict=verdict, days_ago=days).model_dump()


async def _history(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    record: ScreeningRecord,
    *,
    now: datetime.datetime | None,
) -> list[dict[str, object]]:
    """같은 아이의 **이전** 기록들. 최근 순이고 `SCREENING_HISTORY_LIMIT` 에서 자릅니다.

    **아이를 모르면 이력이 없습니다.** `pet_id` 는 NULL 일 수 있습니다 — 아이를 지우면
    FK 가 SET NULL 이고, 기록은 남습니다 (`models/screening_record.py`). 그때 소유자
    전체로 넓히면 **다른 아이의 판정이 "지난번" 으로 섞여** 들어갑니다. 넓히지 않습니다 —
    아래 `pet_id=record.pet_id` 로 이미 그 아이 하나로 좁혀 두므로, `list_accessible` 이
    구성원까지 보는 것은 안전합니다.

    **`before` 로 자릅니다 — 부르는 쪽에서 거를 수 없습니다.** 기준 기록보다 나중 것을 여기서
    걸러 내면 `limit` 이 이미 그 앞에 걸린 뒤라, 기준 기록이 최신 12건 밖일 때 창 안에 나중
    기록만 들어와 이력이 통째로 빕니다. 그리고 걸러 내기 전에는 나중 기록이 "지난번" 으로
    실립니다 — 앱의 기록 화면에서 **옛 기록을 열고 묻는 흐름**이 그 경로입니다 (#79 3번 리뷰).
    기준 기록 자신도 `before` 가 같이 뺍니다. `DONE` 이 아닌 행은 `_narrowed` 가 뺍니다.
    """
    if record.pet_id is None:
        return []
    rows = await screening_repo.list_accessible(
        session,
        app_user_id,
        pet_id=record.pet_id,
        before=record,
        limit=_HISTORY_SCAN_LIMIT,
    )
    entries: list[ScreeningContext] = []
    for row in rows:
        narrowed = _narrowed(row, now=now)
        if narrowed is None:
            continue
        entries.append(ScreeningContext.model_validate(narrowed))
        if len(entries) == SCREENING_HISTORY_LIMIT:
            break
    # 상한은 여기서도 계약이 봅니다 — 위의 `break` 가 사라져도 `max_length` 가 막습니다.
    return ScreeningHistory(entries=entries).model_dump()["entries"]


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
