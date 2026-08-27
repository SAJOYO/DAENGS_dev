"""사용자가 누를 수 있는 다음 행동의 공통 출력 계약.

액션은 판정기가 아니다. 이미 등록된 편집 툴을 한 번의 사용자 선택으로 묶어 보여준다.
버튼 하나가 여러 툴을 부를 수 있으므로 단일 ``edit``가 아니라 ``edits``를 싣는다 —
그래야 기존 refine 턴 경계와 undo 한 칸을 그대로 재사용한다.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    args: dict = Field(default_factory=dict, max_length=10)


class SuggestedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["edits"] = "edits"
    source: Literal["policy", "assistant"]
    edits: list[Edit] = Field(min_length=1, max_length=20)
