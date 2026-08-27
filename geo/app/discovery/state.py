"""편집 상태 — 지도 버튼과 자연어가 **함께** 편집하는 것. 클라이언트가 왕복시킨다.

**두 정책은 다르다. 그래서 타입도 나눈다.**

  target (어디를 갈까)  = 필터.  조건에 안 맞으면 결과 집합에서 사라진다.        → geo.search.find_places
  journey (어떻게 갈까) = 판정.  결과를 빼지 않는다. 경로 계산 방식과 advice만 바꾼다. → journey.engine.snapshot (POST /journey)

이 경계를 넘는 유일한 예외는 `journey.hard_limit` 하나뿐이고, 그건 사용자가 명시적으로 켤 때만 동작한다.
journey 값을 find_places에 넘기거나, target 값으로 경로를 바꾸지 말 것.

무상태 서버: 클라이언트가 state를 되돌려준다. history는 undo용 스택.
서버가 매 요청 다시 뽑는 사실(프로필·날씨·현재 시각)은 여기 없다 → `planning/facts.py`.
클라이언트가 실어 보낼 수 있으면 변조되고, 되돌아오면 노후화된다.
docs/explorations/hospital-search/refine-loop.md, condition-schema.md
"""

from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.discovery.semantics import TimeIntent, Urgency
from app.providers.base import Mode

Sort = Literal["distance", "duration", "open_first"]
CURRENT_STATE_VERSION = 4
# undo 스택 깊이. 되돌림 지점은 **턴당 하나**다 (app/discovery/refine/engine.py)
MAX_HISTORY = 10
PositiveId = Annotated[int, Field(ge=1)]
ShortTag = Annotated[str, Field(min_length=1, max_length=64)]
SymptomText = Annotated[str, Field(min_length=1, max_length=200)]


class ContractModel(BaseModel):
    """클라이언트와 왕복하는 계약. 모르는 값은 조용히 버리지 않는다."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TargetPrefs(ContractModel):
    """어디를 갈까 — **필터**. 결과 집합을 바꾼다.

    시각은 여기 없다 → `EditableState.time_intent`. 영업 판정 시각과 이동의 야간 판정
    시각이 한 필드를 공유하면서 서로 어긋났다 (`app/discovery/semantics.py`).

    아래 둘은 **병원이 표방하는 것**이지 이번 상황이 아니다. "지금 밤이다"는
    `EditableState.time_intent`, "지금 급하다"는 `EditableState.urgency` 다.
    """

    radius_m: int = Field(2000, ge=100, le=20000)
    open_now: bool = False
    night_service: bool = False        # 야간진료를 표방하는 곳만
    emergency_service: bool = False    # 응급을 표방하는 곳만
    # 증상은 **진단이 아니라 사용자의 말 그대로**다. 과목으로 번역하지 않는다 — 그건 진단이고
    # 관할 밖이다 (docs/overview.md). 이 말을 읽던 커뮤니티 근거가 #63 으로, 과목 축이 #64 로
    # 없어져서 **지금 이 필드를 조회하는 곳은 없다.** state 에 남고 diff 에 보이기만 한다.
    symptoms: list[SymptomText] = Field(default_factory=list, max_length=20)
    require_tags: list[ShortTag] = Field(default_factory=list, max_length=20)  # 필수
    exclude_ids: list[PositiveId] = Field(default_factory=list, max_length=100)
    pin_ids: list[PositiveId] = Field(default_factory=list, max_length=100)
    limit: int = Field(20, ge=1, le=100)


class WalkPrefs(ContractModel):
    """**도보로 갈 때만** 의미 있는 하위 설정.

    preferred_mode 가 car 로 바뀌어도 **지우지 않는다.** Transport 는 언제나 walk/car/transit 를
    다 반환하므로 도보 대안에는 계속 적용되고, 사용자가 도보로 돌아오면 설정이 살아 있어야 한다.
    """

    max_walk_min: int | None = Field(None, ge=1, le=1440)
    # `option`·`avoid` 는 여기 없다 (#66). 사용자가 고르던 도보 옵션·피하기는 재료가 없어
    # 판정이 사라졌고, 고를 수 있는 척하는 손잡이만 남아 있었다.


class JourneyPrefs(ContractModel):
    """어떻게 갈까 — **판정**. 결과를 빼지 않는다 (hard_limit 예외).

    **수단과 수단별 설정은 계층이 다르다.**
      preferred_mode  어느 leg 를 앞에 놓을지의 *선호*. Transport 는 늘 셋 다 반환한다
      max_total_min   목적지까지 전체 이동시간. **수단 무관** — 차든 도보든 같은 잣대
      walk{}          도보로 갈 때만 의미 있는 것. 도보 옵션이 차량 판정에 적용되면 안 된다

    car{} · transit{} 슬롯은 아직 없다. 유료도로 회피·환승 최소를 넣으려면 그걸 실제로
    반영하는 경로 제공사가 먼저 있어야 한다 — 설정만 받고 무시하면 사용자에게 거짓말이 된다
    (health_flags 의 heart·obesity 가 그렇게 죽어 있다).
    """

    preferred_mode: Mode | None = None
    max_total_min: int | None = Field(None, ge=1, le=1440)
    hard_limit: bool = False            # True면 상한 초과를 결과에서 제외 — 경계를 넘는 유일한 스위치

    walk: WalkPrefs = Field(default_factory=WalkPrefs)


class EditableState(ContractModel):
    state_version: Literal[CURRENT_STATE_VERSION] = CURRENT_STATE_VERSION
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)

    # --- 상황(context): 사실이지 요구가 아니다. target 과 journey **둘 다**의 입력이다
    time_intent: TimeIntent | None = None    # 시각의 뜻. 없으면 "지금"
    # None = 사용자가 긴급도를 말하지 않음. "normal"은 사용자가 명시적으로 진정시킨 것.
    # 둘을 합치면 규칙 긴급도가 기본값 normal에 항상 눌린다.
    urgency: Urgency | None = None
    # 규칙이 파생한 긴급도는 여기 안 남는다 — 턴마다 RuntimeFacts 에서 다시 뽑는다.
    # state 에 눌어붙으면 undo 도 안 되고, 증상 정규식 오탐 하나가 세션을 응급 모드에 가둔다.

    target: TargetPrefs = Field(default_factory=TargetPrefs)
    journey: JourneyPrefs = Field(default_factory=JourneyPrefs)
    sort: Sort = "distance"         # 표시 정책 — 어느 쪽도 아님
    history: list[dict] = Field(default_factory=list, max_length=MAX_HISTORY)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_state(cls, value: Any) -> Any:
        """옛 payload 를 현재 의미로 명시적으로 옮긴다.

        v1 → v2: `target.night/emergency/at` 의 의미 분리.
        v2 → v3: `target.specialty` 제거 (결정 #64).
        v3 → v4: `journey.walk.option`·`avoid` 제거 (결정 #66).

        지운 축은 **알려진 옛 필드로 보고 버린다** — 캐시된 옛 state 를 들고 오는 클라이언트가
        422 를 맞지 않게. 오타는 여전히 extra=forbid 가 잡는다. `history` 스냅샷도 같은 검증을
        타므로(`validate_history_snapshots`) undo 스택의 옛 상태가 저절로 따라온다.
        """
        if not isinstance(value, dict):
            return value
        data = deepcopy(value)
        version = data.get("state_version", 1)
        if version not in (1, 2, 3, CURRENT_STATE_VERSION):
            raise ValueError(f"unsupported state_version: {version}")

        target = data.get("target")
        if isinstance(target, dict):
            if "night" in target:
                target.setdefault("night_service", target.pop("night"))
            if "emergency" in target:
                target.setdefault("emergency_service", target.pop("emergency"))
            target.pop("specialty", None)          # v2 축. 원천이 없어 #64 로 제거
            legacy_at = target.pop("at", None)
            if legacy_at is not None and data.get("time_intent") is None:
                data["time_intent"] = {"kind": "service_at", "at": legacy_at}

        journey = data.get("journey")
        if isinstance(journey, dict) and isinstance(journey.get("walk"), dict):
            journey["walk"].pop("option", None)     # v3 축. 재료가 없어 #66 으로 제거
            journey["walk"].pop("avoid", None)
        data["state_version"] = CURRENT_STATE_VERSION
        return data

    @field_validator("history")
    @classmethod
    def validate_history_snapshots(cls, value: list[dict]) -> list[dict]:
        """undo용 과거 상태도 현재 상태와 같은 계약으로 검증·이행한다."""
        normalized: list[dict] = []
        for snapshot in value:
            candidate = dict(snapshot)
            candidate["history"] = []
            normalized.append(cls.model_validate(candidate).snapshot())
        return normalized

    def snapshot(self) -> dict:
        return self.model_dump(exclude={"history"})
