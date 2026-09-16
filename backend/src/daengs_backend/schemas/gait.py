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

    # 서버가 계산한 권한 플래그 (Task 19, docs/co-care.md §2). **앱은 이 값만 보고
    # 버튼을 그립니다** — `is_owner` 하나로는 확정(업로더 또는 대표)도 삭제(대표만)도
    # 옳게 못 가리고, 서버 규칙을 앱이 다시 구현하면 언젠가 어긋납니다.
    can_confirm: bool
    can_delete: bool

    # 누가 올렸나. **지금도 그 아이의 구성원일 때만** 닉네임이 실립니다(탈퇴·내보내기
    # 됐으면 None) — 케어 로그의 actor 라벨과 같은 규칙(`services/pet_member.actor_label`).
    # 이 칸이 생기기 전의 옛 기록(actor_app_user_id NULL)도 None 입니다.
    created_by: str | None = None


class GaitRecordListResponse(BaseModel):
    records: list[GaitRecordSummary]
    next_cursor: uuid.UUID | None


class GaitRecordDetail(GaitRecordSummary):
    """단건. summary_for_ui 까지 — internal_feature_vector 는 **여기에도 없습니다.**"""

    quality: dict | None
    summary_for_ui: dict | None
    video_meta: dict | None
    failure_reason: str | None
    # 분석 결과(스켈레톤) 영상을 받을 주소. overlay 가 있고 저장소가 설정됐을 때만 채워집니다.
    # 앱은 이게 있으면 이걸 재생하고, 없으면 기기의 원본을 재생합니다. `has_overlay` 는
    # "존재하나"이고 이 값은 "어디서 받나"입니다 — overlay 가 있어도 저장소 미설정이면 null.
    overlay_url: str | None = None


class GaitDeleteResponse(BaseModel):
    record_id: uuid.UUID
    # 지금은 soft delete 만 — 스토리지 파일 정리는 #78 뒤 비동기로 돕니다.
    deleted: bool


class GaitCompareRequest(BaseModel):
    """비교할 두 기록. **순서는 상관없습니다** — 서버가 날짜로 past/recent 를 정합니다."""

    record_id_a: uuid.UUID
    record_id_b: uuid.UUID


class GaitSideSummary(BaseModel):
    """한쪽 다리의 변화 집계 — **다리별 판정의 정본** (D-063 7단계).

    지금까지 이 판정은 서버와 앱 양쪽에 있었습니다. 서버 `flagged`(= `n_diff >=
    SIDE_MIN_DIFF`)와 앱 `verdictOf()` 가 각자 세었고, **임계값이 우연히 같아서**(둘 다 2)
    결과가 맞아떨어졌을 뿐입니다. 한쪽만 바뀌면 조용히 갈라지므로 서버 하나로 모읍니다.

    ⚠️ **`n_joints` 를 "화면에 그릴 관절 수"로 읽지 마세요.** 두 기록 중 **어느 쪽이든**
       가진 관절만 셉니다 — 양쪽 다 없는 관절은 아예 안 들어갑니다. 실제로 잰 수는
       `n_joints - n_unmeasured` 입니다.
    """

    #: 두 기록의 합집합 기준 이 다리의 관절 수.
    n_joints: int
    #: 그중 "차이 관찰됨" 이 난 수. `flagged` 는 이 값만 봅니다.
    n_diff: int
    #: 차이가 난 관절 이름.
    diff_joints: list[str] = []
    #: **못 잰 관절 수** — 어느 축도 "차이 관찰됨" 이 아니면서 한 축이라도 값이 없는 경우.
    #: 앱이 "비교할 관절이 부족함" 을 말할 때 쓰는 수입니다.
    n_unmeasured: int = 0
    #: 이 다리에서 "여러 관절이 함께 달라졌다" 로 볼지. 임계값은 `compare_v4.SIDE_MIN_DIFF`.
    flagged: bool


class GaitCompareResponse(BaseModel):
    """`compare_records` 의 출력에서 `_dev_only_*` 만 뺀 것 (D-058).

    ⚠️ **`joint_movement_range_comparison` 은 관절마다 dict 입니다** — 안에 x·y 판정이
       따로 들어 있습니다. 그것을 하나로 합치는 규칙은 **일부러 두지 않았습니다**:
       지금 모델은 x·y 의 의미가 다르고(전후 이동 vs 상하 흔들림), 합치는 순간 어느 쪽이
       움직였는지가 사라집니다. 모델이나 비교 로직을 바꿀 때 관절 단위 집계 규칙을
       그때 정의합니다.

    ⚠️ **점수·수치를 담지 않습니다.** `_dev_only_raw_feature_cosine` 같은 값이 화면에
       나오면 사용자가 그것을 건강 점수로 읽습니다.
    """

    status: str
    reason: str | None = None
    recommendation: str | None = None
    record_a: dict | None = None
    record_b: dict | None = None
    message_for_ui: str | None = None
    reliability_note: str | None = None
    version_warning: str | None = None
    diff_threshold_note: str | None = None
    joint_movement_range_comparison: dict = {}
    #: 다리별 변화 집계. 키는 `왼쪽`·`오른쪽`, 좌/우로 안 갈리는 관절 이름은 `전체` 한
    #: 덩어리입니다 — 그래서 키를 enum 으로 못 박지 않습니다(못 박으면 그런 응답이 500 이
    #: 됩니다).
    #:
    #: **legacy 기록끼리의 비교에는 없습니다(`null`).** 옛 기록의 관절 이름은 좌/우로
    #: 갈리지 않아 다리별 판정 자체가 성립하지 않습니다 — 받는 쪽은 `null` 일 때
    #: 자기 판정으로 떨어져야 합니다.
    side_summary: dict[str, GaitSideSummary] | None = None
