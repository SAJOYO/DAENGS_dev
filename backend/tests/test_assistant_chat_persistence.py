"""`POST /assistant/query` 의 대화 저장 배선 (D-046).

무상태 계약(`test_assistant_api.py`)은 그대로 두고, `chat_session_id` + `client_message_id`
가 함께 왔을 때만 달라지는 것을 본다 — 문·소유권·강아지 일치·멱등·실패·역호환·트랜잭션
경계. 실제 앱, 실제 인증. 오케스트레이션 서비스는 표식으로, DB 는 fakes 로 갈아끼운다.

**세션 공장이 계측된다.** 표식 서비스가 불리는 순간 열려 있는 AsyncSession 이 0 이어야
한다는 것이 이 카드의 핵심 불변식이라, 공장이 살아 있는 세션 수를 세고 서비스가 그것을
단언한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.models import ChatSession, ChatTurn
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    PrincipalContext,
)
from daengs_backend.routers import assistant as assistant_router

OWNER = uuid.uuid4()
OTHER = uuid.uuid4()
PET = uuid.uuid4()
QUERY = "우리 개가 산책 중에 짖어요. 어떻게 교육하죠?"


class TrackingFactory:
    """`async_sessionmaker` 대역. 열린 세션 수와 연 횟수를 센다."""

    def __init__(self) -> None:
        self.active = 0
        self.opened = 0

    def __call__(self):
        owner = self

        class Context:
            async def __aenter__(self):
                owner.active += 1
                owner.opened += 1
                return FakeSession()

            async def __aexit__(self, *args):
                owner.active -= 1

        return Context()


def _answered(message: str = "답변입니다") -> AssistantResponse:
    return AssistantResponse(
        request_id="test-request",
        status=AssistantStatus.ANSWERED,
        message=message,
        results=[],
        handoffs=[],
        clarify=None,
    )


class FakeService:
    """`AssistantOrchestrationService.run()` 서명만. 불릴 때 열린 DB 세션 수를 기록한다."""

    def __init__(self, factory: TrackingFactory) -> None:
        self.factory = factory
        self.calls: list[dict[str, Any]] = []
        self.response = _answered()
        self.error: Exception | None = None

    async def run(
        self,
        *,
        query: str,
        principal: PrincipalContext,
        context: dict[str, Any] | None = None,
        requested_capability: str | None = None,
        request_id: str | None = None,
        locale: str = "ko-KR",
    ) -> AssistantResponse:
        self.calls.append(
            {
                "query": query,
                "principal": principal,
                "context": context,
                "requested_capability": requested_capability,
                "sessions_open": self.factory.active,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    result = install(Store(FakeAdmin()), monkeypatch)
    result.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    result.add_app_user(FakeAppUser(kakao_id=2, id=OTHER))
    result.pets.append(FakePet(app_user_id=OWNER, name="네옹", breed="poodle", id=PET))
    return result


@pytest.fixture
def factory() -> TrackingFactory:
    return TrackingFactory()


@pytest.fixture
def service(factory: TrackingFactory) -> FakeService:
    return FakeService(factory)


@pytest.fixture
def client(store: Store, factory: TrackingFactory, service: FakeService) -> Iterator[TestClient]:
    async def fake_session():
        yield FakeSession()

    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = lambda: (
        service
    )
    app.dependency_overrides[assistant_router.get_chat_session_factory] = lambda: factory
    app.dependency_overrides[get_session] = fake_session
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _draft(store: Store, owner: uuid.UUID = OWNER) -> ChatSession:
    row = ChatSession(
        app_user_id=owner, pet_id=PET, title="새 대화", agent_categories=[], last_message_at=None
    )
    row.id = uuid.uuid4()
    row.created_at = store.tick()
    store.chat_sessions.append(row)
    return row


def _seed_turn(store: Store, session: ChatSession, status: str, key: uuid.UUID) -> ChatTurn:
    row = ChatTurn(
        session_id=session.id,
        client_message_id=key,
        processing_status=status,
        user_content=QUERY,
        agent_categories=[],
    )
    if status == "completed":
        row.assistant_content = "답"
        row.assistant_status = "ANSWERED"
        row.request_id = "r"
        row.public_response = _answered("답").model_dump(mode="json")
        row.completed_at = store.tick()
    if status == "failed":
        row.error_code = "PROVIDER_UNAVAILABLE"
        row.completed_at = store.tick()
    row.id = uuid.uuid4()
    row.created_at = store.tick()
    # The service measures staleness against the real clock; a "live" reservation must look
    # live now, not at the fake store's 2026-09-01 epoch.
    row.processing_started_at = datetime.now(UTC) if status == "processing" else row.created_at
    store.chat_turns.append(row)
    return row


def _app(user: uuid.UUID = OWNER) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user, SubjectType.APP)}"}


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(uuid.uuid4(), SubjectType.ADMIN, 'ADMIN')}"}


def _persisted(session: ChatSession, key: uuid.UUID | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "query": QUERY,
        "chat_session_id": str(session.id),
        "client_message_id": str(key or uuid.uuid4()),
        **extra,
    }


def _post(client: TestClient, body: dict[str, Any], headers: dict[str, str] | None = None):
    return client.post("/assistant/query", json=body, headers=headers or {})


# ------------------------------------------------------------------ 라우터


def test_persisted_request_reserves_orchestrates_completes_and_replays_on_read(
    client: TestClient, store: Store, service: FakeService, factory: TrackingFactory
) -> None:
    draft = _draft(store)
    got = _post(client, _persisted(draft), _app())

    assert got.status_code == 200
    assert got.json() == service.response.model_dump(mode="json")
    assert len(service.calls) == 1

    (turn,) = store.chat_turns
    assert turn.processing_status == "completed"
    assert turn.user_content == QUERY
    assert turn.assistant_content == "답변입니다"
    assert turn.public_response == got.json()
    assert draft.last_message_at is not None  # first delivered answer activates the draft
    assert factory.opened == 2 and factory.active == 0  # reserve TX, completion TX

    detail = client.get(f"/app/chats/{draft.id}", headers=_app()).json()
    assert detail["turns"][0]["public_response"] == got.json()
    assert detail["session"]["last_message_at"] is not None


def test_only_assistant_query_writes_turns(client: TestClient) -> None:
    """두 번째 실행 경로는 없다 — `/app/chats/.../turns` 같은 것을 만들지 않았다."""
    paths = app.openapi()["paths"]
    assert "/assistant/query" in paths
    assert not any(path.startswith("/app/chats") and "turn" in path for path in paths)


# ------------------------------------------------------------------- 인증


def test_admin_token_cannot_persist_and_touches_nothing(
    client: TestClient, store: Store, service: FakeService, factory: TrackingFactory
) -> None:
    draft = _draft(store)
    got = _post(client, _persisted(draft), _admin())
    assert got.status_code == 403
    assert got.json()["detail"] == {"code": "CHAT_PERSISTENCE_APP_USER_ONLY"}
    assert service.calls == []
    assert store.chat_turns == []
    assert factory.opened == 0


def test_admin_token_still_works_statelessly(client: TestClient, service: FakeService) -> None:
    assert _post(client, {"query": QUERY}, _admin()).status_code == 200
    assert len(service.calls) == 1


def test_no_token_is_401_before_anything(
    client: TestClient, store: Store, service: FakeService, factory: TrackingFactory
) -> None:
    draft = _draft(store)
    assert _post(client, _persisted(draft)).status_code == 401
    assert service.calls == [] and store.chat_turns == [] and factory.opened == 0


def test_withdrawn_member_with_a_live_token_is_401_and_writes_nothing(
    client: TestClient, store: Store, service: FakeService
) -> None:
    """`admin_or_app_user` 는 토큰만 믿는다. 저장 경로는 `current_app_user` 처럼 다시 본다."""
    draft = _draft(store)
    store.app_users[1].status = "withdrawn"
    got = _post(client, _persisted(draft), _app())
    assert got.status_code == 401
    assert service.calls == [] and store.chat_turns == []


# ---------------------------------------------------------- 소유권 · 강아지


def test_someone_elses_session_is_404_without_reserving(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)  # OWNER's
    got = _post(client, _persisted(draft), _app(OTHER))
    assert got.status_code == 404
    assert service.calls == [] and store.chat_turns == []


def test_unknown_session_is_404(client: TestClient, service: FakeService) -> None:
    body = {"query": QUERY, "chat_session_id": str(uuid.uuid4()), "client_message_id": str(uuid.uuid4())}
    assert _post(client, body, _app()).status_code == 404
    assert service.calls == []


def test_sessions_pet_is_what_the_orchestrator_sees(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)
    assert _post(client, _persisted(draft, source="assistant"), _app()).status_code == 200
    assert service.calls[0]["context"] == {"source": "assistant", "active_dog_id": str(PET)}


def test_matching_active_dog_id_is_accepted(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)
    got = _post(client, _persisted(draft, active_dog_id=str(PET)), _app())
    assert got.status_code == 200
    assert service.calls[0]["context"]["active_dog_id"] == str(PET)


def test_conflicting_active_dog_id_is_409_before_any_row(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)
    got = _post(client, _persisted(draft, active_dog_id="dog-1"), _app())
    assert got.status_code == 409
    assert got.json()["detail"] == {"code": "ACTIVE_DOG_MISMATCH", "session_pet_id": str(PET)}
    assert service.calls == [] and store.chat_turns == []


# ------------------------------------------------------------------ 멱등


def test_same_uuid_same_question_replays_the_stored_response_without_orchestration(
    client: TestClient, store: Store, service: FakeService, factory: TrackingFactory
) -> None:
    draft = _draft(store)
    key = uuid.uuid4()
    first = _post(client, _persisted(draft, key), _app())
    service.response = _answered("두 번째 호출이면 이게 보였을 것")
    second = _post(client, _persisted(draft, key), _app())

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert len(service.calls) == 1
    assert len(store.chat_turns) == 1
    assert factory.opened == 3  # reserve + completion, then the replay's single reserve TX


def test_same_uuid_different_question_is_409_and_keeps_the_original(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)
    key = uuid.uuid4()
    _post(client, _persisted(draft, key), _app())
    got = _post(client, _persisted(draft, key, query="전혀 다른 질문"), _app())
    assert got.status_code == 409
    assert got.json()["detail"] == {
        "code": "CLIENT_MESSAGE_ID_REUSED",
        "turn_id": str(store.chat_turns[0].id),
    }
    assert len(service.calls) == 1
    assert store.chat_turns[0].user_content == QUERY


def test_same_uuid_while_processing_is_409_wait(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)
    key = uuid.uuid4()
    live = _seed_turn(store, draft, "processing", key)
    got = _post(client, _persisted(draft, key), _app())
    assert got.status_code == 409
    assert got.json()["detail"] == {"code": "TURN_PROCESSING", "turn_id": str(live.id)}
    assert service.calls == []


def test_failed_uuid_is_409_and_a_fresh_uuid_retries(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)
    key = uuid.uuid4()
    dead = _seed_turn(store, draft, "failed", key)
    got = _post(client, _persisted(draft, key), _app())
    assert got.status_code == 409
    assert got.json()["detail"] == {
        "code": "TURN_FAILED",
        "turn_id": str(dead.id),
        "error_code": "PROVIDER_UNAVAILABLE",
    }
    assert service.calls == []

    retry = _post(client, _persisted(draft), _app())
    assert retry.status_code == 200
    assert len(store.chat_turns) == 2 and len(service.calls) == 1


# ------------------------------------------------------------------ 실패


def test_orchestration_exception_closes_the_turn_failed_and_propagates_unchanged(
    client: TestClient, store: Store, service: FakeService, factory: TrackingFactory
) -> None:
    draft = _draft(store)
    service.error = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        _post(client, _persisted(draft), _app())

    (turn,) = store.chat_turns
    assert turn.processing_status == "failed"
    assert turn.error_code == "ORCHESTRATION_FAILED"
    assert draft.last_message_at is None
    assert factory.opened == 2 and factory.active == 0  # reserve TX, failure TX

    # 무상태일 때와 같은 오류다 — 저장이 오류 모양을 바꾸지 않는다.
    with pytest.raises(RuntimeError, match="provider down"):
        _post(client, {"query": QUERY}, _app())


def test_failed_status_response_is_returned_but_does_not_activate(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)
    service.response = AssistantResponse(
        request_id="req-failed",
        status=AssistantStatus.FAILED,
        message="요청을 해석하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    )
    got = _post(client, _persisted(draft), _app())
    assert got.status_code == 200
    assert got.json()["status"] == "FAILED"
    (turn,) = store.chat_turns
    assert turn.processing_status == "failed" and turn.error_code == "ASSISTANT_FAILED"
    assert draft.last_message_at is None


def test_delivered_answer_that_cannot_be_stored_is_still_returned(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)
    service.response = _answered("가" * 8_001)
    got = _post(client, _persisted(draft), _app())
    assert got.status_code == 200
    assert len(got.json()["message"]) == 8_001  # not truncated
    (turn,) = store.chat_turns
    assert turn.processing_status == "failed"
    assert turn.error_code == "ASSISTANT_CONTENT_TOO_LONG"
    assert draft.last_message_at is None


def test_thirty_turns_is_409_before_orchestration(
    client: TestClient, store: Store, service: FakeService
) -> None:
    draft = _draft(store)
    for _ in range(30):
        _seed_turn(store, draft, "completed", uuid.uuid4())
    got = _post(client, _persisted(draft), _app())
    assert got.status_code == 409
    assert got.json()["detail"] == {"code": "TURN_LIMIT_EXCEEDED", "limit": 30}
    assert service.calls == []


# -------------------------------------------------------------- 역호환


def test_requests_without_chat_fields_never_open_a_db_session(
    client: TestClient, store: Store, service: FakeService, factory: TrackingFactory
) -> None:
    got = _post(client, {"query": QUERY, "active_dog_id": "dog-1"}, _app())
    assert got.status_code == 200
    assert service.calls[0]["context"] == {"active_dog_id": "dog-1"}  # hint passes through
    assert store.chat_turns == [] and store.chat_sessions == []
    assert factory.opened == 0


def test_question_limit_applies_only_to_persisted_requests(
    client: TestClient, store: Store, service: FakeService, factory: TrackingFactory
) -> None:
    draft = _draft(store)
    long_query = "가" * 2_001

    stateless = _post(client, {"query": long_query}, _app())
    assert stateless.status_code == 200  # the v0.0.0 contract is not narrowed
    assert service.calls[0]["query"] == long_query

    persisted = _post(client, _persisted(draft, query=long_query), _app())
    assert persisted.status_code == 422
    assert persisted.json()["detail"] == {"code": "QUESTION_TOO_LONG", "limit": 2_000}
    assert len(service.calls) == 1 and store.chat_turns == []
    assert factory.opened == 0  # rejected before any session is opened


@pytest.mark.parametrize("present", ["chat_session_id", "client_message_id"])
def test_one_chat_field_without_the_other_is_422(
    client: TestClient, service: FakeService, present: str
) -> None:
    got = _post(client, {"query": QUERY, present: str(uuid.uuid4())}, _app())
    assert got.status_code == 422
    assert service.calls == []


def test_stateless_response_shape_is_unchanged(client: TestClient, service: FakeService) -> None:
    got = _post(client, {"query": QUERY, "requested_capability": "training"}, _app())
    assert got.status_code == 200
    assert got.json() == service.response.model_dump(mode="json")
    assert service.calls[0]["requested_capability"] == "training"


# ------------------------------------------------------- 트랜잭션 경계


def test_no_db_session_is_open_while_the_orchestrator_runs(
    client: TestClient, store: Store, service: FakeService, factory: TrackingFactory
) -> None:
    draft = _draft(store)
    assert _post(client, _persisted(draft), _app()).status_code == 200
    assert service.calls[0]["sessions_open"] == 0
    assert factory.opened == 2  # one short TX before the call, one after — never around it


def test_no_db_session_is_open_while_the_orchestrator_fails(
    client: TestClient, store: Store, service: FakeService, factory: TrackingFactory
) -> None:
    draft = _draft(store)
    service.error = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        _post(client, _persisted(draft), _app())
    assert service.calls[0]["sessions_open"] == 0
    assert factory.active == 0
