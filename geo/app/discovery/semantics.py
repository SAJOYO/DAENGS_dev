"""의미 타입 — 값 하나에 뭉쳐 있던 **뜻**을 가른다. 판정은 여기 없다.

`at` 은 세 가지 뜻이었고 `emergency` 는 두 가지 뜻이었다. 한 필드에 담아두면 소비자가
각자 필요한 대로 해석하고, 그 해석들이 조용히 어긋난다 — 실제로 병원 영업 판정은
`target.at` 을 보고 경로의 야간 판정은 서버 현재 시각을 봤다. 같은 요청인데 두 엔진이
다른 시각을 살았다.

여기 있는 건 값과 병합 규칙뿐이다. 무엇을 거르고 어떤 경로를 고를지는 resolver 와
엔진의 몫이다 (app/discovery/resolver.py — 예정).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

TimeKind = Literal["depart_at", "arrive_by", "service_at"]


class TimeIntent(BaseModel):
    """사용자가 말한 시각의 **뜻**. 셋은 서로 다른 곳으로 흐른다.

      depart_at   "9시에 출발할게"         → 경로의 야간 판정 (JourneyPlan)
      arrive_by   "10시까지 도착해야 해"   → 후보별 실행 가능성 (CandidateAssessment)
      service_at  "내일 오후에 하는 병원"  → 병원 운영시간 판정 (SearchPlan)

    합치면 안 되는 이유: `arrive_by` 에서 출발 시각을 구하려면 후보별 이동시간이 필요한데,
    그건 후보를 뽑고 경로를 계산한 **다음에야** 나온다. 요청 단위로 확정할 수 있는 값이 아니다.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    kind: TimeKind
    at: datetime

    @field_validator("at")
    @classmethod
    def timezone_is_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("time intent must include a timezone")
        return value


# --------------------------------------------------------------------- 긴급도
Urgency = Literal["normal", "urgent"]
UrgencyOrigin = Literal["user", "rule"]

_RANK: dict[Urgency, int] = {"normal": 0, "urgent": 1}


class UrgencySignal(BaseModel):
    """긴급도 하나와 그 **출처**. 출처를 지우면 병합을 못 한다."""

    value: Urgency
    origin: UrgencyOrigin
    reason: str = ""        # "breathing_safety_rule" 등. 사용자에게 이유를 말할 때 쓴다


def safety_urgency(signals: list[UrgencySignal]) -> tuple[Urgency, list[str]]:
    """**안전 표면**용 — 최댓값. 사용자가 "안 급해요" 해도 안전 규칙은 안 사라진다.

    전화 확인 안내·경고 문구가 이 값을 본다. **조건을 좁히지는 않는다** — 안전은
    말해주는 것이지 사용자가 볼 수 있는 병원을 줄이는 게 아니다.
    """
    if not signals:
        return "normal", []
    top = max(_RANK[s.value] for s in signals)
    value: Urgency = "urgent" if top else "normal"
    return value, [s.reason or s.origin for s in signals if _RANK[s.value] == top]


def planning_urgency(signals: list[UrgencySignal]) -> Urgency:
    """**행동 계획**용 — 사용자 명시가 이긴다. 수단 전환·정렬·advice 억제가 이 값을 본다.

    최댓값을 쓰면 안 되는 이유: "예전에 숨을 헐떡인 적이 있어서 검진 받으려고요"에 규칙이
    걸리면 사용자가 "안 급해요"라고 해도 내릴 방법이 없다. 정규식 오탐 하나가 세션 전체를
    응급 UI(차량 우선·정렬 변경·전화 CTA)에 가둔다 — 사용자가 말했는데 시스템이 무시하는
    그 실패다. 안전 문구는 `safety_urgency` 로 그대로 남으므로 잃는 게 없다.
    """
    user = [s for s in signals if s.origin == "user"]
    if user:
        return "urgent" if max(_RANK[s.value] for s in user) else "normal"
    return safety_urgency(signals)[0]
