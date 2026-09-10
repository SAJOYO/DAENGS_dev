"""동결 멀티턴 케이스의 스키마 · 로더.

단일 턴 `QuestionCase` 에 멀티턴을 밀어 넣지 않는다 — 순서 · 대상 턴 · 적용가능성이
없으면 이 카드가 재려는 실패가 표현되지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["user", "assistant"]
#: **상호작용 모드다. 계약 상태 이름(ANSWERED · CLARIFY)이 아니다.**
#: `ASK` 가 어느 상태로 나갈지는 아직 안 정해진 계약이라, 케이스가 그 결정보다 오래 살게
#: 모드로만 적는다.
ExpectedMode = Literal["ANSWER", "ASK", "REDIRECT"]


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    text: str = Field(min_length=1, max_length=2_000)


class ConversationCase(BaseModel):
    """케이스 하나. **개인 식별자 칸이 없는 것이 스키마의 일부다.**"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^cq_[a-z0-9_]+$")
    turns: list[Turn] = Field(min_length=1)
    #: 판정 대상인 assistant 턴의 인덱스.
    target_turns: list[int] = Field(min_length=1)
    #: **그 턴 시점에 실제로 있었던 구조화 상태만.** 나중에 DB 를 다시 읽지 않는다.
    state_snapshot: dict = Field(default_factory=dict)
    #: 사용자 입력이 실제로 더 필요했나. False 면 `ASK` 를 안 했다고 감점하지 않는다.
    user_input_needed: bool
    expected_mode: ExpectedMode
    #: 앞 턴에 사용자의 정정·반복·항의가 있었나. False 면 `repair_success` 는 N/A.
    repair_applicable: bool
    source: str = Field(min_length=1)
    version: int = 1
    note: str = ""

    @model_validator(mode="after")
    def _validate(self) -> ConversationCase:
        if self.turns[0].role != "user":
            raise ValueError("첫 턴은 user 여야 한다")
        for i, turn in enumerate(self.turns):
            expected: Role = "user" if i % 2 == 0 else "assistant"
            if turn.role != expected:
                raise ValueError(f"턴 {i} 는 {expected} 여야 한다")
        for idx in self.target_turns:
            if not 0 <= idx < len(self.turns):
                raise ValueError(f"target_turns {idx} 가 범위를 벗어난다")
            if self.turns[idx].role != "assistant":
                raise ValueError(f"target_turns {idx} 가 assistant 턴이 아니다")
        if self.repair_applicable and min(self.target_turns) < 2:
            raise ValueError("복구는 앞 턴이 있어야 성립한다")
        return self


def load_cases(path: Path) -> list[ConversationCase]:
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    return [ConversationCase.model_validate(row) for row in rows]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
