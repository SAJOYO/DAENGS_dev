"""Turn reservation/completion rules. No production message route exists in this phase."""

import asyncio
import uuid

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
from daengs_backend.services import chat as chat_service

OWNER = uuid.uuid4()
PET = uuid.uuid4()


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


def response(status: AssistantStatus = AssistantStatus.ANSWERED) -> AssistantResponse:
    return AssistantResponse(
        request_id=str(uuid.uuid4()),
        status=status,
        message="이렇게 해 보세요.",
        results=[
            CapabilityResult(
                capability=CapabilityName.TRAINING,
                status=CapabilityStatus.OK,
                data={"answer": "공개 답변"},
                elapsed_ms=1,
            )
        ],
    )


def reserve(draft: ChatSession, question: str = "배변 훈련은 어떻게 하나요?") -> ChatTurn:
    return asyncio.run(
        chat_service.reserve_turn(
            FakeSession(),
            OWNER,
            draft.id,
            client_message_id=uuid.uuid4(),
            question=question,
        )
    )


def test_turn_is_one_processing_exchange(store: Store, draft: ChatSession) -> None:
    turn = reserve(draft)
    assert len(store.chat_turns) == 1
    assert turn.processing_status == "processing"
    assert turn.assistant_content is None


def test_same_session_client_uuid_is_idempotent(store: Store, draft: ChatSession) -> None:
    key = uuid.uuid4()
    first = asyncio.run(
        chat_service.reserve_turn(
            FakeSession(), OWNER, draft.id, client_message_id=key, question="질문"
        )
    )
    second = asyncio.run(
        chat_service.reserve_turn(
            FakeSession(), OWNER, draft.id, client_message_id=key, question="다른 질문"
        )
    )
    assert second.id == first.id
    assert len(store.chat_turns) == 1
    assert first.user_content == "질문"


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
