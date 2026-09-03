"""Turn reservation/completion rules: idempotency states, stale recovery, category union."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install

from daengs_backend.models import ChatSession, ChatTurn
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
)
from daengs_backend.repositories import chat as chat_repo
from daengs_backend.services import chat as chat_service

OWNER = uuid.uuid4()
PET = uuid.uuid4()
#: One minute after the fake store's clock epoch (fakes.Store.clock), so a turn reserved by
#: the fakes is never "older than five minutes" unless a test says so explicitly.
NOW = datetime(2026, 9, 1, 0, 1, tzinfo=UTC)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    result = install(Store(FakeAdmin()), monkeypatch)
    result.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    result.pets.append(FakePet(app_user_id=OWNER, name="네옹", breed="poodle", id=PET))
    return result


@pytest.fixture
def draft(store: Store) -> ChatSession:
    row = ChatSession(
        app_user_id=OWNER,
        pet_id=PET,
        title="새 대화",
        agent_categories=[],
        last_message_at=None,
    )
    row.id = uuid.uuid4()
    store.chat_sessions.append(row)
    return row


def response(
    status: AssistantStatus = AssistantStatus.ANSWERED,
    capability: CapabilityName = CapabilityName.TRAINING,
) -> AssistantResponse:
    return AssistantResponse(
        request_id=str(uuid.uuid4()),
        status=status,
        message="이렇게 해 보세요.",
        results=[
            CapabilityResult(
                capability=capability,
                status=CapabilityStatus.OK,
                data={"answer": "공개 답변"},
                elapsed_ms=1,
            )
        ],
    )


def reserve(
    draft: ChatSession,
    question: str = "배변 훈련은 어떻게 하나요?",
    *,
    client_message_id: uuid.UUID | None = None,
    now: datetime = NOW,
) -> ChatTurn:
    return asyncio.run(
        chat_service.reserve_turn(
            FakeSession(),
            OWNER,
            draft.id,
            client_message_id=client_message_id or uuid.uuid4(),
            question=question,
            now=now,
        )
    )


def complete(turn: ChatTurn, reply: AssistantResponse | None = None) -> ChatTurn:
    return asyncio.run(
        chat_service.complete_turn(FakeSession(), OWNER, turn.id, response=reply or response())
    )


def processing_since(draft: ChatSession, started_at: datetime) -> ChatTurn:
    """A reservation whose request died mid-flight: still ``processing`` since ``started_at``."""
    turn = reserve(draft, now=started_at)
    turn.processing_started_at = started_at
    return turn


def test_turn_is_one_processing_exchange(store: Store, draft: ChatSession) -> None:
    turn = reserve(draft)
    assert len(store.chat_turns) == 1
    assert turn.processing_status == "processing"
    assert turn.assistant_content is None


# ------------------------------------------------------- reused client UUID


def test_same_uuid_and_question_replays_the_completed_turn_without_a_new_row(
    store: Store, draft: ChatSession
) -> None:
    key = uuid.uuid4()
    first = complete(reserve(draft, "질문", client_message_id=key))
    replay = reserve(draft, "질문", client_message_id=key)
    assert replay is first
    assert replay.processing_status == "completed"
    assert len(store.chat_turns) == 1


def test_same_uuid_with_different_question_is_an_explicit_conflict(
    store: Store, draft: ChatSession
) -> None:
    key = uuid.uuid4()
    first = reserve(draft, "질문", client_message_id=key)
    with pytest.raises(chat_service.TurnIdempotencyConflictError) as caught:
        reserve(draft, "다른 질문", client_message_id=key)
    assert caught.value.turn_id == first.id
    assert len(store.chat_turns) == 1
    assert first.user_content == "질문"  # the stored question is never rewritten


def test_different_question_conflicts_even_after_completion(draft: ChatSession) -> None:
    key = uuid.uuid4()
    complete(reserve(draft, "질문", client_message_id=key))
    with pytest.raises(chat_service.TurnIdempotencyConflictError):
        reserve(draft, "다른 질문", client_message_id=key)


def test_same_uuid_while_processing_says_wait(draft: ChatSession) -> None:
    key = uuid.uuid4()
    first = reserve(draft, "질문", client_message_id=key)
    with pytest.raises(chat_service.TurnProcessingError) as caught:
        reserve(draft, "질문", client_message_id=key)
    assert caught.value.turn_id == first.id


def test_failed_uuid_stays_burned_and_a_fresh_one_retries(store: Store, draft: ChatSession) -> None:
    key = uuid.uuid4()
    first = reserve(draft, "질문", client_message_id=key)
    asyncio.run(
        chat_service.fail_turn(FakeSession(), OWNER, first.id, error_code="PROVIDER_UNAVAILABLE")
    )
    with pytest.raises(chat_service.TurnFailedError) as caught:
        reserve(draft, "질문", client_message_id=key)
    assert caught.value.turn_id == first.id
    assert caught.value.error_code == "PROVIDER_UNAVAILABLE"

    retry = reserve(draft, "질문")
    assert retry.id != first.id
    assert retry.processing_status == "processing"
    assert len(store.chat_turns) == 2


# ------------------------------------------------------------ stale turns


def test_processing_older_than_five_minutes_is_failed_lazily(
    store: Store, draft: ChatSession
) -> None:
    stale = processing_since(draft, NOW - timedelta(minutes=5, seconds=1))
    fresh = reserve(draft)
    assert stale.processing_status == "failed"
    assert stale.error_code == "STALE_PROCESSING"
    assert stale.completed_at is not None
    assert fresh.id != stale.id and fresh.processing_status == "processing"


def test_exactly_five_minutes_is_still_processing(draft: ChatSession) -> None:
    live = processing_since(draft, NOW - timedelta(minutes=5))
    reserve(draft)
    assert live.processing_status == "processing"


def test_stale_recovery_happens_before_the_idempotency_check(draft: ChatSession) -> None:
    """Reusing the stale UUID sees a *failed* turn, not a live one — and stays burned."""
    key = uuid.uuid4()
    stale = reserve(draft, "질문", client_message_id=key, now=NOW - timedelta(minutes=6))
    stale.processing_started_at = NOW - timedelta(minutes=6)
    with pytest.raises(chat_service.TurnFailedError) as caught:
        reserve(draft, "질문", client_message_id=key)
    assert caught.value.error_code == "STALE_PROCESSING"
    assert stale.processing_status == "failed"


def test_stale_recovery_is_committed_even_when_the_reservation_is_then_refused(
    draft: ChatSession,
) -> None:
    stale = processing_since(draft, NOW - timedelta(minutes=6))
    session = FakeSession()
    with pytest.raises(chat_service.TurnIdempotencyConflictError):
        asyncio.run(
            chat_service.reserve_turn(
                session,
                OWNER,
                draft.id,
                client_message_id=stale.client_message_id,
                question="다른 질문",
                now=NOW,
            )
        )
    assert stale.processing_status == "failed"
    assert session.commits == 1  # the recovery, committed before the check raised


def test_stale_turns_do_not_count_toward_the_thirty_turn_cap(
    store: Store, draft: ChatSession
) -> None:
    for _ in range(30):
        processing_since(draft, NOW - timedelta(minutes=6))
    fresh = reserve(draft)
    assert fresh.processing_status == "processing"
    assert sum(1 for turn in store.chat_turns if turn.error_code == "STALE_PROCESSING") == 30


def test_late_completion_of_a_stale_turn_does_not_resurrect_it(draft: ChatSession) -> None:
    stale = processing_since(draft, NOW - timedelta(minutes=6))
    reserve(draft)  # recovery
    with pytest.raises(chat_service.CompletionConflictError):
        complete(stale)
    assert stale.processing_status == "failed"
    assert draft.last_message_at is None


# --------------------------------------------------------- category union


def test_session_categories_are_the_union_of_completed_turns(draft: ChatSession) -> None:
    training = reserve(draft, "훈련 질문")
    life = reserve(draft, "생활 질문")
    complete(training, response(capability=CapabilityName.TRAINING))
    complete(life, response(capability=CapabilityName.LIFE))
    assert training.agent_categories == ["training"]
    assert life.agent_categories == ["life"]
    assert draft.agent_categories == ["training", "life"]


def test_category_union_is_computed_from_the_locked_session_row(
    draft: ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The union must start from the row the lock returns, not from a copy read earlier.

    ``get_owned_turn`` hands back a *stale* session snapshot here; only the locked re-read
    carries the ``training`` category that another completion already wrote.
    """
    training = reserve(draft, "훈련 질문")
    life = reserve(draft, "생활 질문")
    complete(training, response(capability=CapabilityName.TRAINING))

    original = chat_repo.get_owned_turn

    async def stale_snapshot(session, app_user_id, turn_id):
        owned = await original(session, app_user_id, turn_id)
        if owned is None:
            return None
        turn, live = owned
        snapshot = ChatSession(
            app_user_id=live.app_user_id,
            pet_id=live.pet_id,
            title=live.title,
            agent_categories=[],  # what a pre-lock read would still be holding
            last_message_at=live.last_message_at,
        )
        snapshot.id = live.id
        return turn, snapshot

    monkeypatch.setattr(chat_repo, "get_owned_turn", stale_snapshot)
    complete(life, response(capability=CapabilityName.LIFE))
    assert draft.agent_categories == ["training", "life"]


def test_completion_persists_safe_public_response(store: Store, draft: ChatSession) -> None:
    turn = reserve(draft)
    saved = asyncio.run(
        chat_service.complete_turn(
            FakeSession(), OWNER, turn.id, response=response()
        )
    )
    assert saved.processing_status == "completed"
    assert saved.assistant_status == "ANSWERED"
    assert saved.agent_categories == ["training"]
    assert saved.public_response["status"] == "ANSWERED"
    assert "provider_payload" not in saved.public_response
    assert "exception" not in saved.public_response


def test_provider_failure_does_not_activate(store: Store, draft: ChatSession) -> None:
    turn = reserve(draft)
    asyncio.run(
        chat_service.fail_turn(
            FakeSession(), OWNER, turn.id, error_code="PROVIDER_UNAVAILABLE"
        )
    )
    assert turn.processing_status == "failed"
    assert turn.error_code == "PROVIDER_UNAVAILABLE"
    assert draft.last_message_at is None


def test_conditional_completion_rejects_second_writer(draft: ChatSession) -> None:
    turn = reserve(draft)
    asyncio.run(chat_service.complete_turn(FakeSession(), OWNER, turn.id, response=response()))
    with pytest.raises(chat_service.CompletionConflictError):
        asyncio.run(
            chat_service.complete_turn(FakeSession(), OWNER, turn.id, response=response())
        )


@pytest.mark.parametrize(
    ("question", "field"),
    [("", "question"), ("가" * 2_001, "question")],
)
def test_question_limit_is_rejected_without_truncation(
    draft: ChatSession, question: str, field: str
) -> None:
    with pytest.raises(chat_service.ContentLimitError) as caught:
        reserve(draft, question)
    assert caught.value.field == field


def test_assistant_limit_fails_reservation_without_activation(
    draft: ChatSession,
) -> None:
    turn = reserve(draft)
    too_long = response().model_copy(update={"message": "가" * 8_001})
    with pytest.raises(chat_service.ContentLimitError):
        asyncio.run(chat_service.complete_turn(FakeSession(), OWNER, turn.id, response=too_long))
    assert turn.processing_status == "failed"
    assert draft.last_message_at is None


def test_thirty_reserved_or_completed_turns_is_the_hard_cap(
    store: Store, draft: ChatSession
) -> None:
    for _ in range(30):
        store.chat_turns.append(
            ChatTurn(
                session_id=draft.id,
                client_message_id=uuid.uuid4(),
                processing_status="completed",
                user_content="질문",
                assistant_content="답",
                assistant_status="ANSWERED",
                request_id=str(uuid.uuid4()),
                agent_categories=[],
                public_response={"status": "ANSWERED"},
            )
        )
    with pytest.raises(chat_service.TurnLimitError):
        reserve(draft)
