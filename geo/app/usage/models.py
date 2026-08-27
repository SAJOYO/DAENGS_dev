"""Usage Gate 계약. 자유형 속성 대신 실제 유료 호출마다 고정된 intent를 둔다."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Literal

from app.providers.base import Mode


class MeteredOperation(StrEnum):
    STATIC_MAP = "map.render_static"
    MEASURED_ROUTE = "route.measure"
    LANGUAGE_PARSE = "language.parse"


@dataclass(frozen=True)
class StaticMapIntent:
    width: int
    height: int
    marker_count: int
    operation: ClassVar[MeteredOperation] = MeteredOperation.STATIC_MAP
    units: ClassVar[int] = 1


@dataclass(frozen=True)
class MeasuredRouteIntent:
    # `option` 은 여기 없다 (#66). 안 읽혀서가 아니라 **상류에서 더 이상 선택될 수 없어서**다 —
    # 기록할 변주가 없다. `mode` 는 정책이 분기하지 않아도 walk/car/transit 로 실제 갈리는
    # 사실이므로 남는다.
    mode: Mode
    operation: ClassVar[MeteredOperation] = MeteredOperation.MEASURED_ROUTE
    units: ClassVar[int] = 1


@dataclass(frozen=True)
class LanguageParseIntent:
    input_chars: int
    operation: ClassVar[MeteredOperation] = MeteredOperation.LANGUAGE_PARSE
    units: ClassVar[int] = 1


type UsageIntent = StaticMapIntent | MeasuredRouteIntent | LanguageParseIntent


@dataclass(frozen=True)
class UsageWindow:
    bucket: str
    max_units: int
    seconds: int


@dataclass(frozen=True)
class UsagePermit:
    allowed: bool
    reason: str
    max_units_per_request: int | None = None
    window: UsageWindow | None = None


type DenialCode = Literal[
    "policy_denied", "request_limit", "usage_limit", "request_scope_missing"
]


class UsageDenied(RuntimeError):
    """실제 provider를 호출하기 전에 Usage Gate가 요청을 거부했다."""

    def __init__(self, code: DenialCode, reason: str, *, retry_after_s: int | None = None):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.retry_after_s = retry_after_s
