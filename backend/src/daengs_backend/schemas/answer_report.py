"""AI 답변 신고의 바깥 모양 (D-053 · 콘솔 로드맵 A1).

**여기가 열람 범위를 지키는 마지막 자리입니다.** D-053 은 원문을 **신고된 turn 하나**로
좁혔습니다. 상세 응답에 세션의 다른 turn 을 담는 필드를 만들지 마세요 — 만들면 그것을
채우는 코드가 따라오고, 결정이 스키마에서 조용히 바뀝니다.

맥락이 필요한 신고인지는 `position` 이 알려 줍니다 — **숫자만**이고 원문이 아닙니다.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from daengs_backend.models.answer_report import REASON_MAX_CHARS

ReportStatus = Literal["open", "reviewed", "dismissed"]


class ReportCreate(BaseModel):
    """앱이 보내는 것. **답변 원문을 받지 않습니다** — `turn_id` 가 그것을 가리킵니다."""

    turn_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=REASON_MAX_CHARS)


class ReportCreated(BaseModel):
    """신고 접수 응답. 앱이 보여 줄 것이 없으므로 id 와 시각만 돌려줍니다."""

    id: uuid.UUID
    created_at: datetime


class ReportReviewer(BaseModel):
    id: uuid.UUID
    login_id: str
    name: str


class ReportListItem(BaseModel):
    """목록 한 줄. **원문이 없습니다** — 목록을 여는 것은 감사에 남기지 않으므로
    (D-053 ③) 여기에 질문·답변을 실으면 그 선이 무너집니다."""

    id: uuid.UUID
    created_at: datetime
    status: ReportStatus
    reason: str
    #: 같은 답변이 몇 번 신고됐는지. 여러 사람이 신고할 수 있습니다.
    report_count: int
    reviewer: ReportReviewer | None = None
    reviewed_at: datetime | None = None


class ReportPage(BaseModel):
    """최근 순 한 쪽. **총 개수를 주지 않습니다** — 감사 목록과 같은 이유입니다."""

    reports: list[ReportListItem]
    next_cursor: str | None = None


class ReportedTurn(BaseModel):
    """신고된 답변 **한 건**. 이 대화의 다른 turn 은 여기 오지 않습니다 (D-053 ①)."""

    id: uuid.UUID
    created_at: datetime
    user_content: str
    assistant_content: str | None
    assistant_status: str | None
    #: 이미 공개 계약인 `AssistantResponse` 그대로입니다 — 근거·상태가 여기 있습니다.
    public_response: dict[str, Any] | None
    #: **LangSmith 트레이스의 run id 와 같은 값입니다** (D-054). 트레이싱을 켜 둔
    #: 기간의 신고라면 이 값으로 그 요청의 라우팅·검색 청크·프롬프트까지 열 수 있습니다.
    #: 콘솔이 링크를 걸 자리이고, 그래서 이 필드는 새로 넣을 것이 없습니다.
    request_id: str | None
    agent_categories: list[str]

    #: 이 대화의 완료 turn 총 수. **원문이 아니라 숫자입니다.**
    session_turn_count: int
    #: 이 turn 이 그중 몇 번째인지 (1부터).
    position: int


class ReportDetail(BaseModel):
    """상세. **이 응답을 만드는 것이 감사에 남습니다** (D-053 ③)."""

    id: uuid.UUID
    created_at: datetime
    status: ReportStatus
    reason: str
    report_count: int
    reviewer: ReportReviewer | None = None
    reviewed_at: datetime | None = None
    turn: ReportedTurn


class ReportStatusPatch(BaseModel):
    """처리. `open` 으로 되돌리는 것은 받지 않습니다 — 처리한 사람과 시각을 지우는
    일이라 감사 기록과 어긋납니다."""

    status: Literal["reviewed", "dismissed"]
