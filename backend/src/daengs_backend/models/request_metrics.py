"""요청 하나가 남기는 것. 원본 스키마는 `db/init/22_request_metrics.sql` 입니다.

**이것은 제품 데이터가 아닙니다.** 지워도 서비스가 안 죽습니다 — 그래서 아무 관계도
없고(`relationship` 이 하나도 없습니다) 아무것도 이 표를 참조하지 않습니다.

⚠ **질문 원문도 회원 식별자도 여기 넣지 마세요.** D-037 이 오케스트레이션의 일반 관측에
질문 원문을, D-054 가 관측 저장소에 회원 식별자를 금지했습니다. 이 클래스의 열 목록이
그 약속의 전부이고, `verify_2026-09-07_request_metrics.sql` 의 ⑤ 가 **이름으로** 그것을
지킵니다 — 열이 하나 늘어도 스키마는 안 깨지고 테스트도 안 터지기 때문입니다.

원문이 필요한 진단은 D-054 의 트레이싱이 따로 답합니다 (옵트인 · 기본 꺼짐 · 보존 30일).
**둘은 겹치지 않습니다** — 이쪽은 집계, 저쪽은 한 건 파고들기입니다.

append-only 라 `updated_at` 이 없습니다 (`admin_audit_log` 와 같습니다).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

#: `principal_kind` 에 들어가는 값. `contracts.PrincipalContext.kind` 와 같습니다.
#: **SQL 에 CHECK 이 있습니다** — `action` 과 달리 이 목록은 카드마다 늘지 않고,
#: 오타가 섞이면 아무 에러 없이 집계가 두 갈래로 갈립니다.
PRINCIPAL_KINDS = ("ADMIN", "APP_USER")

#: `router_kind` 에 들어가는 값. 라우터가 아예 못 돈 요청은 `None` 입니다.
ROUTER_KINDS = ("deterministic", "llm")


class RequestMetric(Base):
    """`/assistant/query` 한 건의 뒷면.

    이름이 단수인 것은 한 행이 요청 하나라서입니다 (`WalkAnalysis` 와 같은 결).
    """

    __tablename__ = "request_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    #: 이 요청의 이름. **세 곳에서 같은 값입니다** — 여기 · `chat_turns.request_id` ·
    #: (트레이싱을 켜면) Cloud Trace 의 trace id (D-054).
    #:
    #: UNIQUE 가 아닙니다. 한 요청이 두 행을 남기는 것은 버그지만, 그 버그 때문에
    #: **답변이 죽으면 안 됩니다** — 이 표의 쓰기가 사용자 응답을 막지 않는 것이
    #: 제일 중요한 성질입니다 (`services/request_metrics.py`).
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    #: 누가 불렀나 — **종류만입니다.**
    principal_kind: Mapped[str] = mapped_column(String(20), nullable=False)

    #: 목적지를 무엇이 골랐나. 라우터가 못 돈 요청은 `None`.
    router_kind: Mapped[str | None] = mapped_column(String(20))

    #: 실제로 실행된 능력들. **빈 배열이 정상입니다** — 사교적 응답과 라우터 실패는
    #: 아무 능력도 안 부릅니다. 그 둘을 "0개 능력" 으로 남기는 것이 이 열의 값입니다.
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'")
    )

    #: 결과. `contracts.AssistantStatus` 와 같은 값.
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    #: 거절 · 기권의 **사유 코드**. 문구가 아닙니다 — 문구는 사용자에게 보이는 말이라
    #: 바뀌고, 바뀌면 집계가 끊깁니다.
    reason_code: Mapped[str | None] = mapped_column(String(60))

    #: 예외 범주. 타입 이름 수준까지고 **메시지는 안 넣습니다** — 예외 메시지에는
    #: 질문이나 좌표가 섞여 들어옵니다.
    error_category: Mapped[str | None] = mapped_column(String(60))

    #: **이 표의 핵심.** 지금 이 값이 아무 데도 없습니다.
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        CheckConstraint("elapsed_ms >= 0", name="request_metrics_elapsed_check"),
        CheckConstraint(
            "principal_kind IN ('ADMIN', 'APP_USER')",
            name="request_metrics_principal_kind_check",
        ),
        CheckConstraint(
            "router_kind IS NULL OR router_kind IN ('deterministic', 'llm')",
            name="request_metrics_router_kind_check",
        ),
    )
