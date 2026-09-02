"""Draft activation and five-active-session retention."""

import asyncio
import uuid

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install

from daengs_backend.models import ChatSession
from daengs_backend.orchestration.contracts import AssistantResponse, AssistantStatus
from daengs_backend.repositories import chat as chat_repo
from daengs_backend.services import chat as chat_service

OWNER = uuid.uuid4()
PET = uuid.uuid4()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    result = install(Store(FakeAdmin()), monkeypatch)
    result.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    result.pets.append(FakePet(app_user_id=OWNER, name="네옹", breed="poodle", id=PET))
    return result


def create_draft() -> ChatSession:
    return asyncio.run(chat_service.create_session(FakeSession(), OWNER, PET))


def delivered(status: AssistantStatus) -> AssistantResponse:
    return AssistantResponse(
        request_id=str(uuid.uuid4()), status=status, message="사용자에게 전달된 응답"
    )


def activate(draft: ChatSession, status: AssistantStatus = AssistantStatus.ANSWERED) -> None:
    turn = asyncio.run(
        chat_service.reserve_turn(
            FakeSession(),
            OWNER,
            draft.id,
            client_message_id=uuid.uuid4(),
            question="질문",
        )
    )
    asyncio.run(
        chat_service.complete_turn(FakeSession(), OWNER, turn.id, response=delivered(status))
    )


def test_only_one_draft_exists_and_it_is_not_listed(store: Store) -> None:
    first = create_draft()
    second = create_draft()
    assert second.id == first.id
    assert len(store.chat_sessions) == 1
    assert asyncio.run(chat_service.list_sessions(FakeSession(), OWNER, PET)) == []


@pytest.mark.parametrize(
    "status",
    [AssistantStatus.CLARIFY, AssistantStatus.HANDOFF, AssistantStatus.REFUSED],
)
def test_every_delivered_assistant_response_activates(
    store: Store, status: AssistantStatus
) -> None:
    draft = create_draft()
    activate(draft, status)
    assert draft.last_message_at is not None
    assert asyncio.run(chat_service.list_sessions(FakeSession(), OWNER, PET)) == [draft]


def test_pet_row_is_locked_only_on_first_activation(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original = chat_repo.lock_owned_pet

    async def counted(session, app_user_id, pet_id):
        nonlocal calls
        calls += 1
        return await original(session, app_user_id, pet_id)

    monkeypatch.setattr(chat_repo, "lock_owned_pet", counted)
    draft = create_draft()
    activate(draft)
    activate(draft)
    assert calls == 1


def test_retention_keeps_five_active_sessions_and_not_the_draft(store: Store) -> None:
    for _ in range(6):
        draft = create_draft()
        activate(draft)
    active = asyncio.run(chat_service.list_sessions(FakeSession(), OWNER, PET))
    assert len(active) == 5

    new_draft = create_draft()
    assert new_draft.last_message_at is None
    assert len(store.chat_sessions) == 6
    assert len(asyncio.run(chat_service.list_sessions(FakeSession(), OWNER, PET))) == 5


def test_session_delete_cascades_turns_but_only_detaches_summary(store: Store) -> None:
    draft = create_draft()
    activate(draft)
    summary = type("Summary", (), {"source_session_id": draft.id})()
    store.chat_summaries.append(summary)
    asyncio.run(chat_service.delete_session(FakeSession(), OWNER, draft.id))
    assert all(turn.session_id != draft.id for turn in store.chat_turns)
    assert summary.source_session_id is None
