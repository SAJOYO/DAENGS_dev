"""능력을 LangChain 툴로 내놓고, **모델이 무엇을 골랐는지만 기록한다.**

v1(#247) 의 툴은 부르는 즉시 어댑터를 돌렸다. 그래서 에이전트는 툴을 하나씩 부르며
알게 되었고, 좌표가 없어 막힌 능력이 있어도 이미 나온 답을 살렸다 — 동결된 계약
(O-8: CLARIFY 는 배타, 실행 전에 선택 전체를 게이트)과 갈리는 자리였고, 비교 v1 의
갈린 5건 중 3건이 그 차이였다 (#252 리포트).

v2(#272) 의 툴은 **아무것도 실행하지 않는다.** 툴 호출은 "이 목적지를 고른다" 는
선언이고, 툴박스는 그것을 `SemanticRoutingDecision` 으로 모은다 — 의미 라우터가 한 번의
구조화 출력으로 내놓는 바로 그 모양이다. 루프가 끝나면 서비스가 그 결정을 공유
`planner.assemble_route_plan` 에 넘기고(좌표 게이트 · payload · 실행 순서), 나온 RoutePlan
을 LangGraph 와 같은 `OrchestrationEngine` 이 실행한다. 툴 함수에 어댑터 참조가 없으므로
**전역 검증 전에 어댑터가 도는 코드 경로 자체가 없다.**

**LLM 은 payload 를 한 글자도 쓰지 않는다.** 툴은 전부 인자가 없다(`reply_socially` 의
`intent` 만 예외이고 그것은 분류값이다). 질문 텍스트와 좌표는 planner 가 신뢰된
query·context 에서만 만든다 — D-051 의 불변식이고, 선택을 기록만 하게 바꿔도 그대로다.

**에이전트는 자기 지식으로 답하지 않는다.** 답이 될 수 있는 모든 출구를 툴로 만든다 —
능력 넷, 핸드오프 둘, 인사말 하나. 출구가 없으면 모델은 자기 말로 답해 버리고, 그건 v1 이
일부러 라우팅하지 않기로 한 일반 육아 질문까지 답하게 된다는 뜻이다 (semantic.py v5·v6).
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool

from daengs_backend.orchestration.contracts import CapabilityName
from daengs_backend.orchestration.semantic import (
    ExecuteName,
    HandoffName,
    SemanticRoutingDecision,
    SocialIntent,
)

_LABELS = {
    CapabilityName.TRAINING: "훈련",
    CapabilityName.LIFE: "생활 정보",
    CapabilityName.WALK: "산책 조건",
    CapabilityName.PLACE: "장소 검색",
}


class CapabilityToolbox:
    """한 요청 동안 살아 있는 툴 묶음이자 그 요청의 **선택 기록**.

    **요청마다 새로 만든다.** 기록이 상태라서, 재사용하면 앞 요청이 고른 능력이 다음
    요청의 결정에 섞인다.
    """

    def __init__(self) -> None:
        #: 부른 순서대로, 중복 없이. 실행 순서는 여기가 아니라 planner 가 정한다.
        self.execute: list[ExecuteName] = []
        self.handoffs: list[HandoffName] = []
        self.social_intent: SocialIntent | None = None

    def decision(self) -> SemanticRoutingDecision:
        """모델의 선택을 의미 라우터와 같은 모양으로.

        **능력 의도가 인사보다 우선한다** — 라우터 스키마의 규칙(`social_intent_is_exclusive`)
        과 같은 뜻이다. 모델이 인사 툴과 능력 툴을 같이 불렀으면 인사는 버린다. 그래야 두
        구현이 같은 발화에서 같은 종류의 답을 낸다.
        """
        social = self.social_intent if not (self.execute or self.handoffs) else None
        return SemanticRoutingDecision(
            execute=list(self.execute), handoffs=list(self.handoffs), social_intent=social
        )

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

    # ── 툴 만들기 ───────────────────────────────────────────────────────

    def _capability_tool(self, capability: CapabilityName, name: str, description: str) -> BaseTool:
        async def _run() -> str:
            if capability.value in self.execute:
                # 같은 능력을 두 번 골라도 기록은 하나다. 거절이 아니라 이미 있는 사실을
                # 다시 알려 주어 루프를 끊는다 — 거절하면 모델이 다른 툴을 찾아 헤맨다.
                return f"(이미 기록했습니다) {_LABELS[capability]} 을(를) 부르기로 했습니다."
            self.execute.append(capability.value)  # type: ignore[arg-type]
            return (
                f"{_LABELS[capability]} 을(를) 부르기로 기록했습니다. "
                "실행은 선택이 끝난 뒤 시스템이 한꺼번에 합니다."
            )

        return StructuredTool.from_function(coroutine=_run, name=name, description=description)

    def _handoff_tool(self, target: HandoffName, name: str, description: str) -> BaseTool:
        async def _run() -> str:
            if target in self.handoffs:
                return "(이미 기록했습니다)"
            self.handoffs.append(target)
            return "전용 흐름으로 안내하도록 기록했습니다. 안내 문구는 자동으로 붙습니다."

        return StructuredTool.from_function(coroutine=_run, name=name, description=description)

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


__all__ = ["CapabilityToolbox"]
