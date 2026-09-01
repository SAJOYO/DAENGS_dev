"""대화 기록 API 의 요청 / 응답 형태.

**능력 이름은 서버 어휘 그대로 나갑니다** — `training` · `life` · `walk`
(`orchestration/contracts.py` `CapabilityName`). 한글 배지 라벨(훈련 · 생활 · 제도)로
접어서 보내지 않습니다. 라벨은 앱의 어휘라 서버가 정하면 라벨을 바꿀 때마다 배포가
딸려 오고, 이미 저장된 행과도 갈라집니다 (`schemas/pet.py` 가 견종 목록을 검사하지
않는 것과 같은 판단).

**멱등 키는 클라이언트가 만듭니다.** 같은 탭을 두 번 눌렀거나 네트워크가 재시도해도
한 번만 들어가야 하는데, 서버는 그 둘을 구분할 방법이 없습니다 —
`walks.client_session_id` 가 이미 쓰는 방식입니다.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 지금 라우팅이 낼 수 있는 능력의 전부. 새 능력을 여기서 만들지 않습니다.
AgentCategory = Literal["training", "life", "walk"]

MessageRole = Literal["user", "assistant"]


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class ChatSessionCreate(BaseModel):
    """새 대화를 엽니다. 제목은 첫 질문에서 서버가 만듭니다."""

    model_config = ConfigDict(extra="forbid")

    #: 어느 아이의 대화인가. **소유권은 서버가 확인합니다** — 남의 강아지 id 를
    #: 넣으면 404 입니다.
    pet_id: uuid.UUID

    #: 앱이 제목을 미리 정할 수 있으면 보냅니다. 없으면 "새 대화" 로 열리고
    #: 첫 질문이 붙을 때 그 질문으로 바뀝니다.
    title: str | None = Field(default=None, max_length=120)


class ChatMessageResponse(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str

    #: 이 답을 만든 능력들. **사용자 메시지는 빈 배열입니다.**
    agent_categories: list[AgentCategory]

    #: 오케스트레이션 최상위 상태. 사용자 메시지는 `null` 입니다.
    assistant_status: str | None
    created_at: datetime


class ChatSessionResponse(BaseModel):
    """목록 카드 한 장. 메시지는 들어 있지 않습니다."""

    id: uuid.UUID
    pet_id: uuid.UUID
    title: str

    #: 카드에 붙일 배지들. **이 대화에 관여한 능력 전부**입니다 —
    #: 훈련과 생활을 함께 물었으면 둘 다 들어갑니다.
    agent_categories: list[AgentCategory]
    created_at: datetime
    updated_at: datetime


class ChatSessionDetailResponse(BaseModel):
    """카드를 눌렀을 때. 그 대화를 되살립니다."""

    session: ChatSessionResponse
    messages: list[ChatMessageResponse]


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionResponse]

    #: 서버가 유지하는 세션 수. **앱에 숫자를 박아 두지 않게** 같이 보냅니다
    #: (`PetListResponse.max_pets` 와 같은 이유).
    max_sessions: int


class ChatSummaryCreate(BaseModel):
    """`AI 요약 후 저장`. 사용자가 누를 때만 부릅니다."""

    model_config = ConfigDict(extra="forbid")

    #: 두 번 눌러도 한 건만 남게 하는 키. **필수입니다** — 요약은 유료 호출이라
    #: 메시지보다 중복이 비쌉니다. 기기가 UUID 하나를 만들어 재시도에도 같은 값을
    #: 보내면 됩니다.
    client_request_id: str = Field(min_length=1, max_length=64)

    @field_validator("client_request_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _reject_blank(value)


class ChatSummaryResponse(BaseModel):
    """보관함에 저장된 요약 한 건. **구조화되어 있습니다.**"""

    id: uuid.UUID
    pet_id: uuid.UUID

    #: 원본 대화. **5개 유지로 밀려났으면 `null`** 입니다 — 요약은 그래도 남습니다.
    #: 앱은 이 값이 비었으면 "원본 대화 없음"으로 보여 주고 이어서 열기를 감춥니다.
    session_id: uuid.UUID | None

    title: str
    question_summary: str
    answer_summary: str
    key_points: list[str]

    #: 원문의 주의·한계. **요약이 이것을 떨어뜨리지 않습니다.**
    cautions: list[str]

    #: 원문에 있던 출처 그대로. 모델이 새로 만들지 않습니다.
    source_citations: list[str]

    agent_categories: list[AgentCategory]

    #: 무엇으로 만들었는지. 프롬프트를 고쳤을 때 옛 요약과 가를 수 있어야 합니다.
    model: str
    prompt_version: str
    source_message_count: int
    created_at: datetime


class ChatSummaryListResponse(BaseModel):
    summaries: list[ChatSummaryResponse]


__all__ = [
    "AgentCategory",
    "ChatMessageResponse",
    "ChatSessionCreate",
    "ChatSessionDetailResponse",
    "ChatSessionListResponse",
    "ChatSessionResponse",
    "ChatSummaryCreate",
    "ChatSummaryListResponse",
    "ChatSummaryResponse",
    "MessageRole",
]
