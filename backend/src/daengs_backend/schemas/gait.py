"""보행 분석 `/app/gait/*` 의 요청·응답 (D-043).

⚠️ **`internal_feature_vector` 필드가 여기 없습니다 — 빠뜨린 게 아닙니다.**
   비교 전용 값이고 화면에 나오면 사용자가 건강 점수로 읽습니다. 응답 스키마가
   그 컬럼을 아예 모르므로 라우터가 실수로도 내보낼 수 없습니다.
   (`response_model` 이 여기 정의된 필드만 통과시킵니다.)
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class GaitAnalyzeRequest(BaseModel):
    pet_id: uuid.UUID
    # 업로드할 파일의 원본 이름 (표시용) 과 타입. presign 발급에 씁니다.
    source_file: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="video/mp4", max_length=100)
    captured_at: datetime.date | None = None
    note: str | None = Field(default=None, max_length=2000)


class GaitUploadTicketResponse(BaseModel):
    """기록이 만들어졌고(PENDING) 앱이 스토리지에 직접 올릴 자리입니다.

    올린 뒤 `POST /app/gait/records/{record_id}/confirm` 을 불러야 분석이 시작됩니다.
    """

    record_id: uuid.UUID
    status: str
    upload_url: str
    upload_headers: dict[str, str]
    expires_in_seconds: int


class GaitRecordSummary(BaseModel):
    """목록용 요약. 무거운 값(quality 세부·trajectories)은 단건 조회로."""

    record_id: uuid.UUID
    pet_id: uuid.UUID
    status: str
    quality_status: str | None
    quality_tier: str | None
    gait_filter_version: str | None
    captured_at: datetime.date | None
    source_file: str | None
    note: str | None
    created_at: datetime.datetime
    # /v1 시절 계약을 이어받는 파생 필드 — 앱이 비교 화면에서 고를 수 있는 것만
    # 보여주는 데 씁니다.
    comparable: bool
    has_overlay: bool


class GaitRecordListResponse(BaseModel):
    records: list[GaitRecordSummary]
    next_cursor: uuid.UUID | None


class GaitRecordDetail(GaitRecordSummary):
    """단건. summary_for_ui 까지 — internal_feature_vector 는 **여기에도 없습니다.**"""

    quality: dict | None
    summary_for_ui: dict | None
    video_meta: dict | None
    failure_reason: str | None


class GaitDeleteResponse(BaseModel):
    record_id: uuid.UUID
    # 지금은 soft delete 만 — 스토리지 파일 정리는 #78 뒤 비동기로 돕니다.
    deleted: bool
