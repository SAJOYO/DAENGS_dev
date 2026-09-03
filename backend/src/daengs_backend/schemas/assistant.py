"""`POST /assistant/query` 의 공개 요청 DTO.

여기서 막아야 하는 것은 orchestration 내부 fail-fast(`orchestration/semantic.py`
`build_semantic_router_prompt`)에 잘못된 모양이 닿지 않게 하는 것이다. 그쪽은
승인된 내부 계약을 신뢰하고 `ValueError` 로 즉시 죽는다 — 외부 입력의 모양을
바로잡는 것은 이 경계의 일이지 orchestration 의 일이 아니다.

`AssistantResponse` (orchestration/contracts.py) 는 이미 승인된 공개 응답 계약이라
(orchestration-contracts.md §5) 여기서는 요청만 다룬다.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Walk 적합도(`GET /walk`)와 WalkPayload(orchestration/contracts.py)가 이미 쓰는
# 남한 좌표 범위. 새 지리 정책을 만들지 않는다 — 세 곳이 같은 값이어야 한다.
_LAT_BOUNDS = (33.0, 39.0)
_LON_BOUNDS = (124.0, 132.0)


class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=_LAT_BOUNDS[0], le=_LAT_BOUNDS[1])
    lon: float = Field(ge=_LON_BOUNDS[0], le=_LON_BOUNDS[1])


def _reject_blank(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("must not be blank")
    return value


class AssistantQueryRequest(BaseModel):
    """클라이언트가 보낼 수 있는 필드 전부. 나머지는 422 로 거부한다."""

    model_config = ConfigDict(extra="forbid")

    query: str
    # 라우팅 신호일 뿐 인가가 아니다 (D-036, orchestration-routing.md §1). 새 능력을
    # 만들지 않는다 — 알 수 없는 값은 planner.resolve_deterministic_route 가 그대로
    # 의미 라우팅으로 넘긴다.
    requested_capability: str | None = None
    # 승인된 라우팅 메타데이터 키뿐이다 (orchestration/semantic.py
    # `_ROUTING_METADATA_KEYS`). 그 밖의 필드는 확장하지 않는다.
    source: str | None = None
    action: str | None = None
    # 소유권 증명이 아니라 라우팅/개인화 힌트일 뿐이다 (O-4, contracts §1).
    active_dog_id: str | None = None
    location: LocationIn | None = None
    # 대화 저장 (D-046). **둘 다 있으면** 이 질문과 답이 그 대화의 turn 으로 남고, **둘 다
    # 없으면** v0.0.0 그대로 무상태다 — 그 요청은 DB 를 한 번도 열지 않는다. 한쪽만 있는
    # 것은 모양이 틀린 것이라 422. 앱 회원 전용이고, 대화의 `pet_id` 가 `active_dog_id` 보다
    # 우선한다 (routers/assistant.py).
    chat_session_id: uuid.UUID | None = None
    client_message_id: uuid.UUID | None = None

    @property
    def persists(self) -> bool:
        return self.chat_session_id is not None

    @model_validator(mode="after")
    def _persistence_fields_come_together(self) -> AssistantQueryRequest:
        if (self.chat_session_id is None) != (self.client_message_id is None):
            raise ValueError("chat_session_id and client_message_id must be sent together")
        return self

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        # min_length=1 만으로는 "   " 를 못 잡는다. 원문은 그대로 두고 검증만 한다 —
        # orchestration 이 원문 보존의 소유자다.
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    @field_validator("requested_capability", "source", "action", "active_dog_id")
    @classmethod
    def _metadata_not_blank(cls, value: str | None) -> str | None:
        return _reject_blank(value)


__all__ = ["AssistantQueryRequest", "LocationIn"]
