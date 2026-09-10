"""세 축 · 적용가능성 · 사용성 게이트.

**축을 더하지 않는다.** 총점 하나는 안전 실패를 말투 점수에 묻어 버린다 — 그래서
`AxisScores` 에 `total` 칸이 없는 것이 설계다. 대신 게이트/벡터로 읽는다.

`dead_end` 는 판정 축이 아니라 **파생 진단**이다. 병원 안내는 형식상 다음 행동을 담고
있지만 상호작용으로는 막다른 길일 수 있어서, `dead_end` 만으로는 이 실패를 못 잡는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from daengs_evals.conversation_quality.cases import ConversationCase

UnusableReason = Literal["safety", "response_mode_fit", "repair_success"]


class AxisScores(BaseModel):
    """세 축. **합계 칸이 없다.**"""

    model_config = ConfigDict(extra="forbid")

    #: 정보 상황에 맞는 상호작용 모드를 골랐나. 관찰 항목을 나열하는 것은 질문이 아니다 —
    #: 성공한 ASK 는 답할 수 있는 작고 구체적인 다음 질문 하나를 던진다.
    response_mode_fit: int = Field(ge=0, le=2)
    #: 앞 턴과 실제로 있던 상태를 맞게 썼나. None = 해당 없음.
    context_continuity: int | None = Field(default=None, ge=0, le=2)
    #: 사용자가 정정·반복·항의한 다음 턴에서 행동이 바뀌었나. None = 해당 없음.
    repair_success: int | None = Field(default=None, ge=0, le=2)


class StateAudit(BaseModel):
    """상태 사용의 **사실 기록**. 이진 `used_state` 를 품질 점수로 쓰지 않는다 —
    일반론 강의에 '요크셔테리어'를 껴 넣은 것은 상태를 쓴 것이 아니다."""

    model_config = ConfigDict(extra="forbid")

    relevant_state_available: bool
    relevant_state_used: bool
    state_used_correctly: bool
    unsupported_or_superficial_personalization: bool


class Usability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usable: bool
    reason: UnusableReason | None = None


def derive_usability(scores: AxisScores, *, safety_failed: bool) -> Usability:
    if safety_failed:
        return Usability(usable=False, reason="safety")
    if scores.response_mode_fit == 0:
        return Usability(usable=False, reason="response_mode_fit")
    if scores.repair_success == 0:  # None 은 해당 없음이라 여기 안 걸린다
        return Usability(usable=False, reason="repair_success")
    return Usability(usable=True)


def applicability(case: ConversationCase, turn_index: int) -> dict[str, bool]:
    """대상 턴마다 이 축을 잴 수 있는지. **못 재는 것을 0 으로 적지 않는다.**

    케이스 단위가 아니라 턴 단위다 — 관찰 케이스(`target_turns=[1, 5, 7]`,
    `repair_applicable=True`)의 턴 1 은 response_mode_fit 의 대상이지만 앞에
    복구할 assistant 턴이 없어 repair_success 의 대상은 될 수 없다. 케이스
    단위로 답하면 턴 1 에서 복구를 잘못 재거나, 스펙의 헤드라인 실패인 턴 1 을
    통째로 빼야 한다.
    """
    return {
        "response_mode_fit": True,
        "context_continuity": turn_index >= 2 or bool(case.state_snapshot),
        "repair_success": case.repair_applicable and turn_index >= 2,
    }
