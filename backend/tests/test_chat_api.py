"""`/app/chats` HTTP 경계 — 요약 삭제·소유권·경로 순서·되살린 turn 의 `public_response`.

DB 에는 붙지 않습니다. 리포지토리는 `fakes.install` 이 갈아 끼우고, 인증은 실제 토큰으로
`current_app_user` 를 통과합니다 — 이 라우터가 `CurrentAppUser` 를 쓴다는 것 자체가
검증 대상이라 문을 오버라이드하지 않습니다 (`test_gait_app_api.py` 와 같은 자리).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.models import ChatSession, ChatSummary, ChatTurn
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    Handoff,
)
from daengs_backend.routers import chat as chat_router

OWNER = uuid.uuid4()
OTHER = uuid.uuid4()
PET = uuid.uuid4()
NOW = datetime(2026, 9, 3, tzinfo=UTC)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    result = install(Store(FakeAdmin()), monkeypatch)
    result.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    result.add_app_user(FakeAppUser(kakao_id=2, id=OTHER))
    result.pets.append(FakePet(app_user_id=OWNER, name="네옹", breed="poodle", id=PET))
    return result


@pytest.fixture
def client(store: Store) -> Iterator[TestClient]:
    async def fake_session():
        yield FakeSession()

    app.dependency_overrides[get_session] = fake_session
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _auth(app_user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(app_user_id, SubjectType.APP)}"}


def _summary(store: Store, status: str = "completed", owner: uuid.UUID = OWNER) -> ChatSummary:
    completed = status == "completed"
    row = ChatSummary(
        app_user_id=owner,
        pet_id=PET,
        source_session_id=None,
        source_turn_count=1,
        client_request_id=uuid.uuid4(),
        processing_status=status,
        title="배변 훈련" if completed else None,
        question_summary="배변 훈련을 물었습니다." if completed else None,
        answer_summary="반복하라고 답했습니다." if completed else None,
        key_points=["반복"] if completed else None,
        cautions=[] if completed else None,
        source_citations=[] if completed else None,
        agent_categories=["training"],
        model="m" if completed else None,
        prompt_version="v" if completed else None,
        error_code="PROVIDER_FAILED" if status == "failed" else None,
        processing_started_at=NOW,
        completed_at=NOW if status != "processing" else None,
    )
    row.id = uuid.uuid4()
    row.created_at = NOW
    store.chat_summaries.append(row)
    return row


def _active_session(store: Store) -> ChatSession:
    row = ChatSession(
        app_user_id=OWNER,
        pet_id=PET,
        title="배변 훈련",
        agent_categories=["training"],
        last_message_at=NOW,
    )
    row.id = uuid.uuid4()
    row.created_at = NOW
    store.chat_sessions.append(row)
    return row


def _delivered() -> AssistantResponse:
    return AssistantResponse(
        request_id="req-1",
        status=AssistantStatus.PARTIAL,
        message="같은 자리에서 반복하세요.",
        results=[
            CapabilityResult(
                capability=CapabilityName.TRAINING,
                status=CapabilityStatus.OK,
                data={"answer": "같은 자리에서 반복하세요.", "citations": ["동물보호법 제8조"]},
                elapsed_ms=12,
            )
        ],
        handoffs=[Handoff(target="vet", reason="증상이 이어지면 진료가 필요합니다")],
    )


def _turn(store: Store, session: ChatSession, status: str, **over) -> ChatTurn:
    fields = {
        "session_id": session.id,
        "client_message_id": uuid.uuid4(),
        "processing_status": status,
        "user_content": "배변 훈련 어떻게 해요?",
        "agent_categories": [],
    }
    if status == "completed":
        delivered = _delivered()
        fields.update(
            assistant_content=delivered.message,
            assistant_status=delivered.status.value,
            request_id=delivered.request_id,
            agent_categories=["training"],
            public_response=delivered.model_dump(mode="json"),
            completed_at=NOW,
        )
    if status == "failed":
        fields.update(error_code="PROVIDER_UNAVAILABLE", completed_at=NOW)
    fields.update(over)
    row = ChatTurn(**fields)
    row.id = uuid.uuid4()
    row.created_at = row.processing_started_at = store.tick()
    store.chat_turns.append(row)
    return row


# ------------------------------------------------------------ 요약 삭제


def test_owner_deletes_a_completed_summary_and_it_leaves_the_archive(
    client: TestClient, store: Store
) -> None:
    summary = _summary(store)
    got = client.delete(f"/app/chats/summaries/{summary.id}", headers=_auth(OWNER))
    assert got.status_code == 204
    assert store.chat_summaries == []
    listed = client.get("/app/chats/summaries", params={"pet_id": str(PET)}, headers=_auth(OWNER))
    assert listed.json() == {"summaries": []}


def test_someone_elses_summary_is_404_and_stays(client: TestClient, store: Store) -> None:
    summary = _summary(store)
    got = client.delete(f"/app/chats/summaries/{summary.id}", headers=_auth(OTHER))
    assert got.status_code == 404
    assert store.chat_summaries == [summary]


def test_unknown_summary_is_404(client: TestClient) -> None:
    got = client.delete(f"/app/chats/summaries/{uuid.uuid4()}", headers=_auth(OWNER))
    assert got.status_code == 404


def test_processing_summary_is_409_with_its_id(client: TestClient, store: Store) -> None:
    summary = _summary(store, status="processing")
    got = client.delete(f"/app/chats/summaries/{summary.id}", headers=_auth(OWNER))
    assert got.status_code == 409
    assert got.json()["detail"] == {"code": "SUMMARY_PROCESSING", "summary_id": str(summary.id)}
    assert store.chat_summaries == [summary]


def test_failed_reservation_is_not_in_the_archive_so_it_is_404(
    client: TestClient, store: Store
) -> None:
    summary = _summary(store, status="failed")
    got = client.delete(f"/app/chats/summaries/{summary.id}", headers=_auth(OWNER))
    assert got.status_code == 404
    assert store.chat_summaries == [summary]


def test_deleting_a_summary_needs_app_auth(client: TestClient, store: Store) -> None:
    summary = _summary(store)
    assert client.delete(f"/app/chats/summaries/{summary.id}").status_code == 401
    assert store.chat_summaries == [summary]


def test_deleting_a_summary_does_not_touch_the_source_conversation(
    client: TestClient, store: Store
) -> None:
    session = _active_session(store)
    _turn(store, session, "completed")
    summary = _summary(store)
    summary.source_session_id = session.id
    assert client.delete(f"/app/chats/summaries/{summary.id}", headers=_auth(OWNER)).status_code == 204
    assert store.chat_sessions == [session]
    assert len(store.chat_turns) == 1


# ------------------------------------------------------------ 경로 순서


def test_summaries_routes_are_registered_before_the_session_id_routes() -> None:
    """`/summaries/...` 가 뒤에 오면 "summaries" 를 UUID 로 파싱하려다 422 가 난다."""
    paths = [route.path for route in chat_router.router.routes]
    summaries = [i for i, path in enumerate(paths) if path.startswith("/app/chats/summaries")]
    by_session = [i for i, path in enumerate(paths) if path.startswith("/app/chats/{session_id}")]
    assert summaries and by_session
    assert max(summaries) < min(by_session)


def test_delete_summaries_literal_is_not_parsed_as_a_session_id(
    client: TestClient, store: Store
) -> None:
    summary = _summary(store)
    got = client.delete(f"/app/chats/summaries/{summary.id}", headers=_auth(OWNER))
    assert got.status_code != 422


# -------------------------------------------------- 되살린 public_response


def test_completed_turn_replays_the_typed_public_response(
    client: TestClient, store: Store
) -> None:
    session = _active_session(store)
    _turn(store, session, "completed")
    got = client.get(f"/app/chats/{session.id}", headers=_auth(OWNER))
    assert got.status_code == 200
    replayed = got.json()["turns"][0]["public_response"]
    assert replayed == _delivered().model_dump(mode="json")
    assert replayed["handoffs"] == [
        {"target": "vet", "reason": "증상이 이어지면 진료가 필요합니다"}
    ]
    assert replayed["results"][0]["data"]["citations"] == ["동물보호법 제8조"]


@pytest.mark.parametrize("status", ["processing", "failed"])
def test_turns_without_a_delivered_response_replay_null(
    client: TestClient, store: Store, status: str
) -> None:
    session = _active_session(store)
    _turn(store, session, status)
    got = client.get(f"/app/chats/{session.id}", headers=_auth(OWNER))
    assert got.status_code == 200
    turn = got.json()["turns"][0]
    assert turn["public_response"] is None
    assert turn["processing_status"] == status


def test_replayed_response_exposes_only_the_public_contract(
    client: TestClient, store: Store
) -> None:
    session = _active_session(store)
    _turn(store, session, "completed")
    replayed = client.get(f"/app/chats/{session.id}", headers=_auth(OWNER)).json()["turns"][0][
        "public_response"
    ]
    assert set(replayed) == set(AssistantResponse.model_fields)
    for forbidden in ("prompt", "exception", "provider_payload", "api_key", "token"):
        assert forbidden not in str(replayed).lower()


def test_a_stored_row_with_foreign_keys_is_refused_not_leaked(
    client: TestClient, store: Store
) -> None:
    """`AssistantResponse` 는 `extra="forbid"` 다 — 계약 밖 키가 저장돼 있으면 응답에 실려
    나가는 대신 여기서 터져야 한다 (`TestClient` 는 서버 예외를 그대로 올린다)."""
    session = _active_session(store)
    poisoned = _delivered().model_dump(mode="json") | {"prompt": "system: ..."}
    _turn(store, session, "completed", public_response=poisoned)
    with pytest.raises(Exception, match="prompt"):
        client.get(f"/app/chats/{session.id}", headers=_auth(OWNER))
