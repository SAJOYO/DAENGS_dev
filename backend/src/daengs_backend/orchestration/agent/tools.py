"""능력 어댑터를 LangChain 툴로 감싸고, **무엇이 실제로 불렸는지 기록한다.**

LangGraph 는 먼저 RoutePlan 을 짜고 그대로 실행한다. 에이전트는 계획을 만들지 않고
툴을 고르며 루프를 돈다. 그래서 이 툴박스가 두 가지를 한다.

1. **어댑터 실행** — `adapters/` 와 `planner._payload_for` 를 그대로 쓴다. 두 구현이
   같은 능력을 같은 입력으로 부르지 않으면 비교가 "누가 더 나은 payload 를 만드나"가
   되어 버린다.
2. **호출 기록** — 실제로 부른 `CapabilityRequest` 를 남긴다. 이게 없으면 카드 ③이
   잴 것이 없다. `evals/orchestration_router/gold_v1.jsonl` 은 **RoutePlan** 정확도를
   재는데 에이전트는 RoutePlan 을 만들지 않으므로, 여기 쌓인 요청 목록을 나중에
   RoutePlan 모양으로 합쳐 골드의 능력 집합과 비교한다.

**LLM 은 payload 를 한 글자도 쓰지 않는다.** 툴은 전부 인자가 없고, 질문 텍스트와
좌표는 `planner._payload_for` 가 신뢰된 query·context 에서만 만든다 — D-051 이 세운
불변식이고, 에이전트로 바꾼다고 느슨해질 이유가 없다. 특히 좌표: 질의에 지역 이름이
나와도 그것이 좌표가 되지 않는다 (D-051 Option B).

**에이전트는 자기 지식으로 답하지 않는다.** 툴 결과만이 답의 재료다. `service.py` 의
시스템 프롬프트가 그것을 말하고, 여기서는 그 말이 지켜질 수 있도록 **답이 될 수 있는
모든 출구를 툴로 만든다** — 능력 넷, 핸드오프 둘, 그리고 인사말(`reply_socially`).
출구가 없으면 모델은 그냥 자기 말로 답해 버리고, 그건 v1 이 일부러 라우팅하지 않기로
한 일반 육아 질문까지 답하게 된다는 뜻이다 (semantic.py v5·v6 정책).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any, Protocol

from langchain_core.tools import BaseTool, StructuredTool

from daengs_backend.orchestration.adapters import (
    LifeCapabilityAdapter,
    PlaceCapabilityAdapter,
    TrainingCapabilityAdapter,
    WalkCapabilityAdapter,
)

# `_result_message` 는 CapabilityResult 하나를 사람 문장으로 읽는 규칙이다. 모델에게
# 보여 줄 관찰(observation)도 같은 규칙이라야 한다 — 두 벌로 갈리면 모델이 본 것과
# 사용자가 받는 것이 어긋난다. private 이름을 넘어 가져오는 것은 그래서다.
from daengs_backend.orchestration.aggregate import _result_message
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ClarifyRequest,
    ErrorDetail,
    Handoff,
)
from daengs_backend.orchestration.planner import (
    _HANDOFF_REASONS,
    _clarify_question,
    _missing_coordinates,
    _payload_for,
)
from daengs_backend.orchestration.semantic import SocialIntent

_NEEDS_COORDINATES = frozenset({CapabilityName.WALK, CapabilityName.PLACE})


class CapabilityAdapter(Protocol):
    capability: CapabilityName

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult: ...


class CapabilityToolbox:
    """한 요청 동안 살아 있는 툴 묶음이자 그 요청의 실행 기록.

    **요청마다 새로 만든다.** 기록이 상태라서, 재사용하면 앞 요청이 부른 능력이 다음
    요청의 RoutePlan 에 섞인다.
    """

    def __init__(
        self,
        *,
        query: str,
        context: dict[str, Any],
        request_id: str,
        adapters: Mapping[CapabilityName, CapabilityAdapter] | None = None,
    ) -> None:
        if adapters is None:
            adapters = {
                CapabilityName.TRAINING: TrainingCapabilityAdapter(),
                CapabilityName.LIFE: LifeCapabilityAdapter(),
                CapabilityName.WALK: WalkCapabilityAdapter(),
                CapabilityName.PLACE: PlaceCapabilityAdapter(),
            }
        self._adapters = dict(adapters)
        self._query = query
        self._context = context
        self._request_id = request_id

        self.requests: list[CapabilityRequest] = []
        self.results: list[CapabilityResult] = []
        self.handoffs: list[Handoff] = []
        self.clarify: ClarifyRequest | None = None
        self.social_intent: SocialIntent | None = None
        #: 좌표가 없어 막힌 능력들. CLARIFY 문구를 합쳐 고르는 데만 쓴다.
        self._blocked: set[str] = set()

    @property
    def called_capabilities(self) -> set[CapabilityName]:
        """카드 ③이 골드와 맞대는 축. RoutePlan 이 없는 쪽의 유일한 공통 좌표다."""
        return {request.capability for request in self.requests}

    def as_tools(self) -> list[BaseTool]:
        tools = [
            self._capability_tool(
                CapabilityName.TRAINING,
                "ask_training",
                "강아지의 행동을 바꾸거나 무언가를 가르치는 훈련 질문에 답한다. "
                "짖음·물기·배변·산책 줄 당김 같은 행동 교정이 여기다.",
            ),
            self._capability_tool(
                CapabilityName.LIFE,
                "ask_life",
                "제도·법령·행정·정책·계약에 관한 공식 정보를 근거와 함께 답한다. "
                "등록, 기관, 절차, 자격, 지원사업, 요금, 기한, 보험 약관, 운송 약관이 여기다. "
                "일반적인 사육·돌봄 조언은 여기가 아니다.",
            ),
            self._capability_tool(
                CapabilityName.WALK,
                "check_walk_conditions",
                "지금 산책하기 좋은 환경인지 판단한다(날씨·더위·추위·비·대기질). "
                "'어디로 갈까'가 아니라 '지금 나가도 되나'에 답한다. 위치가 필요하다.",
            ),
            self._capability_tool(
                CapabilityName.PLACE,
                "search_places",
                "사용자 근처에서 갈 만한 곳을 찾는다. 장소 종류·목적·묘사로 찾는다. "
                "'지금 나가도 되나'가 아니라 '어디로 갈까'에 답한다. 위치가 필요하다.",
            ),
        ]
        tools.extend(
            [
                self._handoff_tool(
                    "skin",
                    "hand_off_to_skin",
                    "눈에 보이는 피부 상태를 사진으로 확인하는 전용 흐름으로 넘긴다. "
                    "여기서 진단하지 않는다.",
                ),
                self._handoff_tool(
                    "gait",
                    "hand_off_to_gait",
                    "걸음걸이·절뚝임·비대칭·보폭·자세를 영상으로 확인하는 전용 흐름으로 "
                    "넘긴다. 여기서 판단하지 않는다.",
                ),
                self._social_tool(),
            ]
        )
        return tools

    # ── 실행 ────────────────────────────────────────────────────────────

    async def run_request(self, request: CapabilityRequest) -> CapabilityResult:
        """이미 만들어진 요청 하나를 실행하고 기록한다.

        `graph.py._execute_requests` 와 같은 실패 봉쇄다 — 한 어댑터의 예외가 요청
        전체를 죽이지 않고 그 능력의 ERROR 로 남는다. 타임아웃도 같은 뜻이다:
        응답 데드라인이지 실행 취소가 아니다.
        """
        adapter = self._adapters.get(request.capability)
        started = time.perf_counter()
        if adapter is None:
            result = CapabilityResult(
                capability=request.capability,
                status=CapabilityStatus.ERROR,
                error=ErrorDetail(
                    kind="unsupported_capability",
                    detail=f"지원하지 않는 기능입니다: {request.capability.value}",
                ),
                elapsed_ms=0,
            )
        else:
            try:
                pending = adapter.run(request, request_id=self._request_id)
                if request.timeout_ms is None:
                    result = await pending
                else:
                    result = await asyncio.wait_for(pending, timeout=request.timeout_ms / 1_000)
            except TimeoutError:
                result = CapabilityResult(
                    capability=request.capability,
                    status=CapabilityStatus.TIMEOUT,
                    error=ErrorDetail(
                        kind="orchestration_timeout", detail="기능 실행 시간이 초과됐습니다."
                    ),
                    elapsed_ms=_elapsed_ms(started),
                )
            except Exception as exc:  # noqa: BLE001 - contain one adapter's failure
                result = CapabilityResult(
                    capability=request.capability,
                    status=CapabilityStatus.ERROR,
                    error=ErrorDetail(
                        kind=type(exc).__name__,
                        detail="기능 실행 중 예기치 않은 오류가 발생했습니다.",
                    ),
                    elapsed_ms=_elapsed_ms(started),
                )
        self.requests.append(request)
        self.results.append(result)
        return result

    # ── 툴 만들기 ───────────────────────────────────────────────────────

    def _capability_tool(
        self, capability: CapabilityName, name: str, description: str
    ) -> BaseTool:
        async def _run() -> str:
            if capability in self.called_capabilities:
                # 같은 능력을 두 번 부르면 결과가 두 줄로 나가고 비용만 는다.
                # 거절이 아니라 이미 있는 관찰을 다시 주어 루프를 끊는다.
                previous = next(r for r in self.results if r.capability == capability)
                return f"(이미 실행했습니다) {_observation(previous)}"
            if capability in _NEEDS_COORDINATES:
                missing = _missing_coordinates(self._context)
                if missing:
                    self._record_clarify(capability, missing)
                    return (
                        "위치를 알 수 없어 실행하지 못했습니다. 다른 도구로 답할 수 없다면 "
                        "더 이상 도구를 부르지 마세요 — 위치를 묻는 답이 자동으로 나갑니다."
                    )
            request = CapabilityRequest.model_validate(
                {
                    "capability": capability.value,
                    "payload": _payload_for(
                        capability.value, query=self._query, context=self._context
                    ),
                    "timeout_ms": None,
                }
            )
            return _observation(await self.run_request(request))

        return StructuredTool.from_function(
            coroutine=_run, name=name, description=description
        )

    def _handoff_tool(self, target: str, name: str, description: str) -> BaseTool:
        async def _run() -> str:
            if any(handoff.target == target for handoff in self.handoffs):
                return "(이미 안내했습니다)"
            self.handoffs.append(Handoff(target=target, reason=_HANDOFF_REASONS[target]))
            return "전용 흐름으로 안내하도록 기록했습니다. 안내 문구는 자동으로 붙습니다."

        return StructuredTool.from_function(
            coroutine=_run, name=name, description=description
        )

    def _social_tool(self) -> BaseTool:
        async def _run(intent: SocialIntent) -> str:
            """intent: greeting | thanks | goodbye"""
            self.social_intent = intent
            return "인사 문구를 보내도록 기록했습니다. 문구는 자동으로 붙습니다."

        return StructuredTool.from_function(
            coroutine=_run,
            name="reply_socially",
            description=(
                "발화 전체가 인사·감사·작별뿐이고 실제로 요청하는 것이 없을 때만 부른다. "
                "intent 는 greeting · thanks · goodbye 중 하나. "
                "요청이 하나라도 섞여 있으면 그 능력을 부른다."
            ),
        )

    def _record_clarify(self, capability: CapabilityName, missing: list[str]) -> None:
        """좌표가 없어 막힌 것을 남긴다. 문구는 planner 의 것을 그대로 쓴다.

        **여러 능력이 막히면 합쳐서 묻는다** — planner 가 선택 전체를 한 번에 게이트하는
        것과 같은 뜻이다. 산책과 장소를 둘 다 부르려다 막혔는데 "산책할 위치의 위도를
        알려주세요" 가 두 번 나가면 안 된다. 그래서 막힌 능력을 누적해 두고 매번 그
        전체로 문구를 다시 고른다 — `_clarify_question` 은 문구만 돌려주고 "무엇 때문에
        물었나"를 안 들고 오기 때문이다.
        """
        self._blocked.add(capability.value)
        self.clarify = ClarifyRequest(
            question=_clarify_question(missing, needs=self._blocked), missing=missing
        )


def _observation(result: CapabilityResult) -> str:
    """모델이 다음 수를 정하려고 보는 한 줄. 상태를 숨기지 않는다."""
    message = _result_message(result, multiple=False)
    return f"[{result.status.value}] {message}" if message else f"[{result.status.value}]"


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)


__all__ = ["CapabilityToolbox"]
