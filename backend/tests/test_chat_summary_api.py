"""`POST /app/chats/{session_id}/summary` — 토큰만 보는 문과 서비스 소유 TX 경계.

2026-09-03 서버 Phase 3A 에서 이 엔드포인트가 PostgreSQL 자기 교착으로 영영 멈췄다.
`CurrentAppUser` 가 요청 세션에서 `app_users FOR UPDATE` 를 들고 있는 채로 서비스가
`SessionLocal` 로 두 번째 세션을 열어 `chat_summaries` 를 INSERT 했고, 그 INSERT 의 FK 가
같은 행의 `FOR KEY SHARE` 를 기다렸다. 바깥 요청 TX 는 INSERT 를, INSERT 는 요청 TX 를
기다렸다 — 공급자는 한 번도 불리지 않았다.

여기서는 그 배선이 다시 생기면 **결정론적으로** 터지게 한다. DB 에는 붙지 않는다 —
`LockLedger` 가 PostgreSQL 의 두 잠금(`app_users FOR UPDATE` 와 INSERT 의 FK `KEY SHARE`)
을 흉내 내어, 다른 **열린** 트랜잭션이 잡은 행을 기다려야 하는 순간 AssertionError 를 낸다.
실제 DB 라면 그 자리가 영원한 대기다. 인증은 실제 토큰으로 실제 문을 지난다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from daengs_backend.core import database as database_module
from daengs_backend.core import deps as deps_module
from daengs_backend.core import token as token_module
from daengs_backend.core.database import get_chat_session_factory, get_session
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.models import ChatSession, ChatSummary, ChatTurn
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    PrincipalContext,
)
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import chat as chat_repo
from daengs_backend.routers import assistant as assistant_router
from daengs_backend.routers import chat as chat_router
from daengs_backend.services import chat as chat_service
from daengs_backend.services.chat_summary import GeminiChatSummarizer

OWNER = uuid.uuid4()
OTHER = uuid.uuid4()
PET = uuid.uuid4()
NOW = datetime(2026, 9, 3, tzinfo=UTC)
GOOD = {
    "title": "배변 훈련",
    "question_summary": "배변 훈련을 물었습니다.",
    "answer_summary": "반복하라고 답했습니다.",
    "key_points": ["반복"],
    "cautions": [],
    "source_citations": [{"label": "동물보호법 제8조", "url": None}],
}


# ------------------------------------------------------------ 잠금 대역


class TrackedSession(FakeSession):
    """열려 있는지를 안다. `LockLedger` 가 "다른 열린 트랜잭션" 을 가려내는 데 쓴다."""

    def __init__(self) -> None:
        super().__init__()
        self.open = True


class LockLedger:
    """PostgreSQL 행 잠금 흉내 — 실제라면 **기다릴** 자리를 AssertionError 로 바꾼다.

    - `app_users FOR UPDATE`: 다른 열린 세션이 같은 행을 잡고 있으면 막힌다.
    - `chat_summaries` INSERT 의 FK `FOR KEY SHARE`: 다른 열린 세션이 그 회원 행을
      `FOR UPDATE` 로 잡고 있으면 막힌다. **같은** 세션이 잡고 있으면 막히지 않는다 —
      이것이 예약 TX 가 잠금과 INSERT 를 한 트랜잭션에서 하는 이유다.
    """

    def __init__(self) -> None:
        self.holders: dict[uuid.UUID, TrackedSession] = {}
        self.lock_events: list[tuple[uuid.UUID, TrackedSession]] = []

    def _live_other_holder(
        self, app_user_id: uuid.UUID, session: object
    ) -> TrackedSession | None:
        holder = self.holders.get(app_user_id)
        if holder is None or holder is session or not holder.open:
            return None
        return holder

    def lock_for_update(self, session: TrackedSession, app_user_id: uuid.UUID) -> None:
        if self._live_other_holder(app_user_id, session) is not None:
            raise AssertionError(
                "app_users FOR UPDATE would wait on another open transaction"
            )
        self.holders[app_user_id] = session
        self.lock_events.append((app_user_id, session))

    def fk_key_share(self, session: object, app_user_id: uuid.UUID) -> None:
        if self._live_other_holder(app_user_id, session) is not None:
            raise AssertionError(
                "chat_summaries INSERT FK KEY SHARE would wait on an app_users row "
                "held FOR UPDATE by another open transaction — self-deadlock"
            )

    def release(self, session: object) -> None:
        for app_user_id, holder in list(self.holders.items()):
            if holder is session:
                del self.holders[app_user_id]

    @property
    def held(self) -> int:
        return sum(1 for holder in self.holders.values() if holder.open)


class TrackingFactory:
    """`async_sessionmaker` 대역. 열린 세션 수·연 횟수를 세고 닫힐 때 잠금을 푼다."""

    def __init__(self, ledger: LockLedger) -> None:
        self.ledger = ledger
        self.active = 0
        self.opened = 0
        self.sessions: list[TrackedSession] = []

    def __call__(self):
        owner = self

        class Context:
            async def __aenter__(self):
                owner.active += 1
                owner.opened += 1
                session = TrackedSession()
                owner.sessions.append(session)
                self.session = session
                return session

            async def __aexit__(self, *args):
                self.session.open = False
                owner.ledger.release(self.session)
                owner.active -= 1

        return Context()


class RequestSessionGate:
    """요청 수명 `get_session` 대역. 불린 횟수와 지금 살아 있는 수를 센다."""

    def __init__(self, ledger: LockLedger) -> None:
        self.ledger = ledger
        self.calls = 0
        self.active = 0

    async def __call__(self):
        self.calls += 1
        self.active += 1
        session = TrackedSession()
        try:
            yield session
        finally:
            session.open = False
            self.ledger.release(session)
            self.active -= 1


class FakeProvider:
    """요약 공급자의 `generate`. 불릴 때 살아 있는 세션·잠금 수를 기록한다."""

    def __init__(self, factory: TrackingFactory, gate: RequestSessionGate, ledger: LockLedger):
        self.factory = factory
        self.gate = gate
        self.ledger = ledger
        self.calls: list[dict[str, int]] = []
        self.result: dict[str, Any] = dict(GOOD)
        self.error: Exception | None = None
        self.on_call: Callable[[], None] | None = None

    async def __call__(self, prompt: str) -> object:
        self.calls.append(
            {
                "sessions_open": self.factory.active,
                "request_sessions_open": self.gate.active,
                "locks_held": self.ledger.held,
            }
        )
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return self.result


# ------------------------------------------------------------ fixtures


@pytest.fixture
def ledger() -> LockLedger:
    return LockLedger()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, ledger: LockLedger) -> Store:
    result = install(Store(FakeAdmin()), monkeypatch)
    result.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    result.add_app_user(FakeAppUser(kakao_id=2, id=OTHER))
    result.pets.append(FakePet(app_user_id=OWNER, name="네옹", breed="poodle", id=PET))

    # `install` 의 가짜 위에 잠금 장부를 얹는다. 잠금 의미만 더하고 결과는 그대로다.
    plain_lock = app_user_repo.get_active_for_update
    plain_add_summary = chat_repo.add_summary

    async def locking_get_active_for_update(session, app_user_id):
        user = await plain_lock(session, app_user_id)
        if user is not None:
            ledger.lock_for_update(session, app_user_id)
        return user

    def fk_checked_add_summary(session, row):
        ledger.fk_key_share(session, row.app_user_id)
        return plain_add_summary(session, row)

    monkeypatch.setattr(app_user_repo, "get_active_for_update", locking_get_active_for_update)
    monkeypatch.setattr(chat_repo, "add_summary", fk_checked_add_summary)
    return result


@pytest.fixture
def factory(ledger: LockLedger) -> TrackingFactory:
    return TrackingFactory(ledger)


@pytest.fixture
def gate(ledger: LockLedger) -> RequestSessionGate:
    return RequestSessionGate(ledger)


@pytest.fixture
def provider(factory: TrackingFactory, gate: RequestSessionGate, ledger: LockLedger) -> FakeProvider:
    return FakeProvider(factory, gate, ledger)


@pytest.fixture
def client(
    store: Store, factory: TrackingFactory, gate: RequestSessionGate, provider: FakeProvider
) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = gate
    app.dependency_overrides[get_chat_session_factory] = lambda: factory
    app.dependency_overrides[chat_router.get_chat_summarizer] = lambda: GeminiChatSummarizer(
        generate=provider
    )
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _conversation(store: Store, owner: uuid.UUID = OWNER) -> ChatSession:
    session = ChatSession(
        app_user_id=owner,
        pet_id=PET,
        title="배변 훈련",
        agent_categories=["training"],
        last_message_at=NOW,
    )
    session.id = uuid.uuid4()
    session.created_at = NOW
    store.chat_sessions.append(session)
    turn = ChatTurn(
        session_id=session.id,
        client_message_id=uuid.uuid4(),
        processing_status="completed",
        user_content="배변 훈련 어떻게 해요?",
        assistant_content="같은 자리에서 반복하세요.",
        assistant_status="ANSWERED",
        request_id="r",
        agent_categories=["training"],
        public_response={
            "request_id": "r",
            "status": "ANSWERED",
            "message": "같은 자리에서 반복하세요.",
            "results": [],
            "handoffs": [],
        },
        completed_at=NOW,
    )
    turn.id = uuid.uuid4()
    turn.created_at = turn.processing_started_at = NOW
    store.chat_turns.append(turn)
    return session


def _withdraw(store: Store, app_user_id: uuid.UUID = OWNER) -> None:
    """탈퇴 TX 의 commit 이 이 순간 끝난 것처럼 — 상태와 명시 정리(`delete_all_for_user`)."""
    for user in store.app_users.values():
        if user.id == app_user_id:
            user.status = "withdrawn"
    owned = {s.id for s in store.chat_sessions if s.app_user_id == app_user_id}
    store.chat_sessions = [s for s in store.chat_sessions if s.app_user_id != app_user_id]
    store.chat_turns = [t for t in store.chat_turns if t.session_id not in owned]
    store.chat_summaries = [s for s in store.chat_summaries if s.app_user_id != app_user_id]


def _app(user: uuid.UUID = OWNER) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user, SubjectType.APP)}"}


def _admin() -> dict[str, str]:
    token = create_access_token(uuid.uuid4(), SubjectType.ADMIN, "ADMIN")
    return {"Authorization": f"Bearer {token}"}


def _post_summary(
    client: TestClient,
    session: ChatSession,
    headers: dict[str, str] | None,
    request_id: uuid.UUID | None = None,
):
    return client.post(
        f"/app/chats/{session.id}/summary",
        json={"client_request_id": str(request_id or uuid.uuid4())},
        headers=headers or {},
    )


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", repr(dependant.call)))
        stack.extend(dependant.dependencies)
    return names


def _route(path: str, method: str) -> APIRoute:
    # `app.routes` 는 include 된 라우터를 감싸 두므로 라우터의 것을 직접 본다
    # (`test_chat_api.py` 의 경로 순서 테스트와 같은 자리).
    for route in chat_router.router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"route not found: {method} {path}")


# ------------------------------------------------ 배선 회귀 (정적)


def test_summary_route_has_no_request_session_and_no_current_app_user() -> None:
    """`CurrentAppUser` + 요청 세션으로 되돌리면 여기서 바로 떨어진다."""
    names = _dependency_names(_route("/app/chats/{session_id}/summary", "POST"))
    assert "current_app_member_token_only" in names
    assert "get_chat_session_factory" in names
    assert "current_app_user" not in names
    assert "get_session" not in names


def test_other_chat_routes_keep_current_app_user_and_the_request_session() -> None:
    """토큰만 보는 문은 요약에만. 보통의 앱 API 는 그대로 요청 세션에서 active 를 본다."""
    for path, method in [
        ("/app/chats", "GET"),
        ("/app/chats", "POST"),
        ("/app/chats/summaries", "GET"),
        ("/app/chats/summaries/{summary_id}", "DELETE"),
        ("/app/chats/{session_id}", "GET"),
        ("/app/chats/{session_id}", "DELETE"),
    ]:
        names = _dependency_names(_route(path, method))
        assert "current_app_user" in names, (path, method)
        assert "get_session" in names, (path, method)
        assert "current_app_member_token_only" not in names, (path, method)


def test_token_only_dependency_opens_no_database_session() -> None:
    import inspect

    parameters = inspect.signature(deps_module.current_app_member_token_only).parameters
    assert list(parameters) == ["request"]
    assert "get_session" not in _dependency_names(
        _route("/app/chats/{session_id}/summary", "POST")
    )


def test_assistant_and_summary_share_one_session_factory_dependency() -> None:
    assert assistant_router.get_chat_session_factory is database_module.get_chat_session_factory


# ---------------------------------------------- 배선 회귀 (결정론적)


def test_the_old_wiring_is_a_self_deadlock_and_the_ledger_catches_it(
    store: Store, factory: TrackingFactory, gate: RequestSessionGate
) -> None:
    """옛 배선을 손으로 재현한다: 요청 세션이 `app_users FOR UPDATE` 를 든 채 두 번째 세션이
    `chat_summaries` 를 INSERT. 실제 PostgreSQL 은 여기서 영원히 기다렸다 (Phase 3A)."""
    import asyncio

    conversation = _conversation(store)

    async def old_wiring() -> None:
        request_scoped = gate()
        request_session = await request_scoped.__anext__()  # `CurrentAppUser` 의 자리
        try:
            assert await app_user_repo.get_active_for_update(request_session, OWNER)
            async with factory() as second_session:  # `create_summary(SessionLocal, ...)`
                await chat_service.reserve_summary(
                    second_session, OWNER, conversation.id, client_request_id=uuid.uuid4()
                )
        finally:
            await request_scoped.aclose()

    with pytest.raises(AssertionError, match="self-deadlock"):
        asyncio.run(old_wiring())
    assert store.chat_summaries == []


def test_the_new_wiring_locks_and_inserts_in_one_transaction(
    client: TestClient, store: Store, ledger: LockLedger, factory: TrackingFactory
) -> None:
    conversation = _conversation(store)
    got = _post_summary(client, conversation, _app())
    assert got.status_code == 201
    reserve_session = factory.sessions[0]
    assert (OWNER, reserve_session) in ledger.lock_events  # locked in the reservation TX
    assert reserve_session.commits == 1 and not reserve_session.open


# ------------------------------------------------------------ 인증


def test_summary_creation_never_opens_the_request_scoped_session(
    client: TestClient, store: Store, gate: RequestSessionGate
) -> None:
    conversation = _conversation(store)
    assert _post_summary(client, conversation, _app()).status_code == 201
    assert gate.calls == 0


def test_admin_token_is_401_for_the_summary_route(
    client: TestClient, store: Store, provider: FakeProvider, factory: TrackingFactory
) -> None:
    conversation = _conversation(store)
    got = _post_summary(client, conversation, _admin())
    assert got.status_code == 401
    assert provider.calls == [] and store.chat_summaries == []
    assert factory.opened == 0


def test_missing_garbage_and_expired_tokens_are_401(
    client: TestClient, store: Store, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation = _conversation(store)
    assert _post_summary(client, conversation, None).status_code == 401
    assert _post_summary(client, conversation, {"Authorization": "Bearer nope"}).status_code == 401

    monkeypatch.setattr(token_module, "ACCESS_TTL", timedelta(minutes=-1))
    expired = _app()
    monkeypatch.undo()
    assert _post_summary(client, conversation, expired).status_code == 401
    assert provider.calls == [] and store.chat_summaries == []


def test_withdrawn_member_is_401_and_no_summary_is_reserved(
    client: TestClient, store: Store, provider: FakeProvider, factory: TrackingFactory
) -> None:
    """토큰만 보는 문을 지나도 예약 TX 의 active 확인이 막는다 — `/assistant/query` 와 같은 401."""
    conversation = _conversation(store)
    store.app_users[1].status = "withdrawn"
    got = _post_summary(client, conversation, _app())
    assert got.status_code == 401
    assert got.json()["detail"] == "다시 로그인해 주세요."
    assert store.chat_summaries == []
    assert provider.calls == []
    assert factory.opened == 1 and factory.active == 0


def test_active_member_reserves_and_gets_201(
    client: TestClient, store: Store, provider: FakeProvider
) -> None:
    conversation = _conversation(store)
    got = _post_summary(client, conversation, _app())
    assert got.status_code == 201
    (row,) = store.chat_summaries
    assert row.processing_status == "completed"
    assert got.json()["id"] == str(row.id)
    assert got.json()["title"] == GOOD["title"]
    assert len(provider.calls) == 1


def test_someone_elses_conversation_is_404_without_reserving(
    client: TestClient, store: Store, provider: FakeProvider
) -> None:
    conversation = _conversation(store)  # OWNER's
    assert _post_summary(client, conversation, _app(OTHER)).status_code == 404
    assert provider.calls == [] and store.chat_summaries == []


# ------------------------------------------------------- 트랜잭션 경계


def test_no_session_or_lock_from_auth_or_reservation_is_open_while_the_summarizer_runs(
    client: TestClient, store: Store, provider: FakeProvider, factory: TrackingFactory
) -> None:
    conversation = _conversation(store)
    assert _post_summary(client, conversation, _app()).status_code == 201
    assert provider.calls == [
        {"sessions_open": 0, "request_sessions_open": 0, "locks_held": 0}
    ]
    assert factory.opened == 2 and factory.active == 0  # reservation TX, completion TX


def test_no_session_or_lock_is_open_while_the_summarizer_fails(
    client: TestClient, store: Store, provider: FakeProvider, factory: TrackingFactory
) -> None:
    conversation = _conversation(store)
    provider.error = RuntimeError("provider down")
    assert _post_summary(client, conversation, _app()).status_code == 502
    assert provider.calls[0] == {"sessions_open": 0, "request_sessions_open": 0, "locks_held": 0}
    assert factory.active == 0
    (row,) = store.chat_summaries
    assert row.processing_status == "failed" and row.error_code == "SUMMARY_GENERATION_FAILED"


def test_201_is_returned_only_after_the_completed_summary_is_committed(
    client: TestClient, store: Store, provider: FakeProvider, factory: TrackingFactory
) -> None:
    conversation = _conversation(store)

    def still_processing_during_generation() -> None:
        (row,) = store.chat_summaries
        assert row.processing_status == "processing"

    provider.on_call = still_processing_during_generation
    got = _post_summary(client, conversation, _app())
    assert got.status_code == 201
    (row,) = store.chat_summaries
    assert row.processing_status == "completed"
    completion_session = factory.sessions[-1]
    assert completion_session.commits == 1 and not completion_session.open


def test_a_reservation_closed_during_generation_is_503_not_201(
    client: TestClient, store: Store, provider: FakeProvider
) -> None:
    """다른 예약의 5분 stale 회수가 먼저 닫은 행. 늦은 완료가 되살리지 않고 503 이다."""
    conversation = _conversation(store)

    def stale_recovery_closes_it() -> None:
        (row,) = store.chat_summaries
        row.processing_status = "failed"
        row.error_code = "STALE_PROCESSING"

    provider.on_call = stale_recovery_closes_it
    got = _post_summary(client, conversation, _app())
    assert got.status_code == 503
    (row,) = store.chat_summaries
    assert got.json()["detail"] == {
        "code": "SUMMARY_PERSISTENCE_FAILED",
        "summary_id": str(row.id),
        "persistence_error_code": "COMPLETION_CONFLICT",
        "retry_with_fresh_client_request_id": True,
    }
    assert "title" not in got.json()
    assert row.processing_status == "failed" and row.error_code == "STALE_PROCESSING"


# ------------------------------------------------------------ 멱등


def test_existing_summary_for_the_same_source_is_409_without_calling_the_provider(
    client: TestClient, store: Store, provider: FakeProvider
) -> None:
    conversation = _conversation(store)
    first = _post_summary(client, conversation, _app())
    assert first.status_code == 201
    second = _post_summary(client, conversation, _app())
    assert second.status_code == 409
    assert second.json()["detail"] == {
        "code": "SUMMARY_ALREADY_EXISTS",
        "summary_id": first.json()["id"],
    }
    assert len(provider.calls) == 1 and len(store.chat_summaries) == 1


def test_failed_request_id_is_409_and_a_fresh_one_retries(
    client: TestClient, store: Store, provider: FakeProvider
) -> None:
    conversation = _conversation(store)
    burned = uuid.uuid4()
    provider.error = RuntimeError("provider down")
    assert _post_summary(client, conversation, _app(), burned).status_code == 502

    replay = _post_summary(client, conversation, _app(), burned)
    assert replay.status_code == 409
    assert replay.json()["detail"] == {"code": "SUMMARY_REQUEST_ALREADY_FAILED"}

    provider.error = None
    retry = _post_summary(client, conversation, _app())
    assert retry.status_code == 201
    assert [row.processing_status for row in store.chat_summaries] == ["failed", "completed"]
    assert len(provider.calls) == 2


# ------------------------------------------------------- 탈퇴 경쟁


def test_withdrawal_during_the_provider_call_is_401_and_resurrects_nothing(
    client: TestClient, store: Store, provider: FakeProvider, factory: TrackingFactory
) -> None:
    """예약 commit 뒤, 완료 전에 탈퇴가 commit 된다. 매달리지도, 지워진 행을 되살리지도,
    거짓 성공을 돌려주지도 않는다."""
    conversation = _conversation(store)
    provider.on_call = lambda: _withdraw(store)

    got = _post_summary(client, conversation, _app())

    assert got.status_code == 401
    assert got.json()["detail"] == "다시 로그인해 주세요."
    assert len(provider.calls) == 1
    assert store.chat_summaries == [] and store.chat_sessions == [] and store.chat_turns == []
    assert factory.opened == 2 and factory.active == 0  # reservation TX, then the completion attempt
    completion_session = factory.sessions[-1]
    assert completion_session.commits == 0


def test_withdrawal_during_a_failing_provider_call_is_502_and_writes_nothing(
    client: TestClient, store: Store, provider: FakeProvider, factory: TrackingFactory
) -> None:
    conversation = _conversation(store)
    provider.error = RuntimeError("provider down")
    provider.on_call = lambda: _withdraw(store)

    got = _post_summary(client, conversation, _app())

    assert got.status_code == 502
    assert store.chat_summaries == []
    assert factory.active == 0
    assert factory.sessions[-1].commits == 0  # the conditional fail UPDATE matched nothing


# ------------------------------------------ 다른 엔드포인트는 그대로


def test_other_chat_endpoints_still_check_active_in_the_request_session(
    client: TestClient, store: Store, gate: RequestSessionGate, ledger: LockLedger
) -> None:
    conversation = _conversation(store)

    ok = client.get(f"/app/chats/{conversation.id}", headers=_app())
    assert ok.status_code == 200
    assert gate.calls == 1 and gate.active == 0 and ledger.held == 0

    assert client.get(f"/app/chats/{conversation.id}", headers=_admin()).status_code == 401
    assert client.get(f"/app/chats/{conversation.id}").status_code == 401

    store.app_users[1].status = "withdrawn"
    gone = client.get(f"/app/chats/{conversation.id}", headers=_app())
    assert gone.status_code == 401
    assert gone.json()["detail"] == "다시 로그인해 주세요."
    listed = client.get("/app/chats", params={"pet_id": str(PET)}, headers=_app())
    assert listed.status_code == 401


def test_persisted_assistant_query_keeps_its_boundary_and_401(
    client: TestClient, store: Store, factory: TrackingFactory, gate: RequestSessionGate
) -> None:
    """같은 공장 의존성을 쓰는 `/assistant/query` 의 저장 경로는 달라지지 않았다."""
    seen: list[int] = []

    class Service:
        async def run(self, *, query: str, principal: PrincipalContext, context=None, **_):
            seen.append(factory.active)
            return AssistantResponse(
                request_id="r", status=AssistantStatus.ANSWERED, message="답", results=[], handoffs=[]
            )

    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = Service
    draft = ChatSession(
        app_user_id=OWNER, pet_id=PET, title="새 대화", agent_categories=[], last_message_at=None
    )
    draft.id = uuid.uuid4()
    draft.created_at = NOW
    store.chat_sessions.append(draft)
    body = {
        "query": "산책 중에 짖어요",
        "chat_session_id": str(draft.id),
        "client_message_id": str(uuid.uuid4()),
    }

    assert client.post("/assistant/query", json=body, headers=_app()).status_code == 200
    assert seen == [0] and factory.opened == 2 and gate.calls == 0

    store.app_users[1].status = "withdrawn"
    body["client_message_id"] = str(uuid.uuid4())
    assert client.post("/assistant/query", json=body, headers=_app()).status_code == 401
    assert len(seen) == 1


# ------------------------------------------------------------ OpenAPI


def test_openapi_documents_the_summary_persistence_failure(client: TestClient) -> None:
    operation = app.openapi()["paths"]["/app/chats/{session_id}/summary"]["post"]
    description = operation["responses"]["503"]["description"]
    assert "SUMMARY_PERSISTENCE_FAILED" in description
    assert "persistence_error_code" in description
    assert "retry_with_fresh_client_request_id: true" in description
    assert "401" in operation["responses"]


def test_summary_rows_are_never_reinserted_by_completion(store: Store) -> None:
    """완료·실패 TX 는 조건부 UPDATE 뿐이다 — 지워진 예약을 되살릴 INSERT 경로가 없다."""
    import asyncio

    row = ChatSummary(
        app_user_id=OWNER,
        pet_id=PET,
        source_session_id=None,
        source_turn_count=1,
        client_request_id=uuid.uuid4(),
        processing_status="processing",
        agent_categories=[],
        processing_started_at=NOW,
    )
    row.id = uuid.uuid4()
    vanished = row.id  # never added to the store: withdrawal already deleted it

    async def run() -> None:
        session = TrackedSession()
        await chat_service.fail_summary(session, vanished, error_code="X")
        assert session.commits == 0 and session.rollbacks == 1
        with pytest.raises(chat_service.CompletionConflictError):
            from daengs_backend.services.chat_summary import ChatSummaryDraft

            await chat_service.complete_summary(
                session, vanished, ChatSummaryDraft.model_validate(GOOD)
            )

    asyncio.run(run())
    assert store.chat_summaries == []
