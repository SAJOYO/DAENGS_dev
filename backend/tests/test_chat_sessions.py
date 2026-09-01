"""routers/chat.py + services/chat.py — 대화 기록.

DB 는 쓰지 않습니다. `fakes.py` 가 리포지토리를 바꿔치기하므로 여기서 보는 것은
**규칙**입니다 — 스코프가 갈리는가, 여섯 번째에 무엇이 사라지는가, 남의 것을
지우지 않는가, 두 번 눌렀을 때 두 벌이 쌓이는가.

FK 의 실제 동작(CASCADE · SET NULL)은 `test_chat_schema.py` 가 모델과 SQL 로 봅니다.
"""

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.models import ChatSession
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
)
from daengs_backend.routers import chat as chat_router
from daengs_backend.services import chat as chat_service
from fakes import (
    FakeAdmin,
    FakeAppUser,
    FakeChatSummary,
    FakePet,
    FakeSession,
    Store,
    install,
)

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    s.add_app_user(FakeAppUser(kakao_id=2, id=STRANGER))
    return s


@pytest.fixture
def pets(store: Store) -> dict[str, FakePet]:
    """내 아이 둘과 남의 아이 하나. 스코프가 갈리는지 보려면 셋이 필요합니다."""
    mine = FakePet(app_user_id=OWNER, name="네옹", breed="toy_poodle")
    other = FakePet(app_user_id=OWNER, name="두찌", breed="maltese")
    stranger = FakePet(app_user_id=STRANGER, name="남의집", breed="bichon")
    store.pets.extend([mine, other, stranger])
    return {"mine": mine, "other": other, "stranger": stranger}


@pytest.fixture
def client(store: Store) -> TestClient:
    """인증을 통과한 상태로 고정합니다. 토큰 검증은 test_app_auth 가 봅니다."""
    app = FastAPI()
    app.include_router(chat_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=OWNER)
    return TestClient(app, raise_server_exceptions=False)


def _answer(
    *capabilities: str,
    status: AssistantStatus = AssistantStatus.ANSWERED,
    message: str = "이렇게 해 보세요.",
) -> AssistantResponse:
    """능력이 답을 낸 응답. 배지는 여기서 나옵니다."""
    return AssistantResponse(
        request_id=str(uuid.uuid4()),
        status=status,
        message=message,
        results=[
            CapabilityResult(
                capability=CapabilityName(name),
                status=CapabilityStatus.OK,
                data={"answer": message},
                elapsed_ms=1,
            )
            for name in capabilities
        ],
    )


# --- 스코프 ------------------------------------------------------------------


def test_목록은_내_계정과_그_강아지의_것만_준다(
    client: TestClient, store: Store, pets: dict[str, FakePet]
) -> None:
    """다른 아이의 대화도, 남의 대화도 섞이면 안 됩니다."""
    client.post("/app/chats", json={"pet_id": str(pets["mine"].id), "title": "내 아이"})
    client.post("/app/chats", json={"pet_id": str(pets["other"].id), "title": "다른 아이"})

    r = client.get("/app/chats", params={"pet_id": str(pets["mine"].id)})
    assert r.status_code == 200, r.text
    titles = [s["title"] for s in r.json()["sessions"]]
    assert titles == ["내 아이"]


def test_남의_강아지로는_목록을_못_본다(
    client: TestClient, pets: dict[str, FakePet]
) -> None:
    """빈 목록이 아니라 404 입니다 — "그 강아지는 있다"를 알려 주지 않습니다."""
    r = client.get("/app/chats", params={"pet_id": str(pets["stranger"].id)})
    assert r.status_code == 404


def test_남의_강아지로는_대화를_못_연다(
    client: TestClient, pets: dict[str, FakePet]
) -> None:
    r = client.post("/app/chats", json={"pet_id": str(pets["stranger"].id)})
    assert r.status_code == 404


def test_남의_대화는_열지도_지우지도_못한다(
    client: TestClient, store: Store, pets: dict[str, FakePet]
) -> None:
    """소유자가 아니면 존재 자체를 알려 주지 않습니다 (둘 다 404)."""
    intruder = ChatSession(
        app_user_id=STRANGER,
        pet_id=pets["stranger"].id,
        title="남의 대화",
        agent_categories=[],
    )
    intruder.id = uuid.uuid4()
    store.chat_sessions.append(intruder)

    assert client.get(f"/app/chats/{intruder.id}").status_code == 404
    assert client.delete(f"/app/chats/{intruder.id}").status_code == 404
    # 그대로 남아 있어야 합니다.
    assert intruder in store.chat_sessions


# --- 5개 유지 ----------------------------------------------------------------


def test_최근_다섯_개를_최근_갱신_순으로_준다(
    client: TestClient, pets: dict[str, FakePet]
) -> None:
    pet_id = str(pets["mine"].id)
    for i in range(5):
        client.post("/app/chats", json={"pet_id": pet_id, "title": f"대화 {i}"})

    r = client.get("/app/chats", params={"pet_id": pet_id})
    titles = [s["title"] for s in r.json()["sessions"]]
    assert titles == ["대화 4", "대화 3", "대화 2", "대화 1", "대화 0"]
    assert r.json()["max_sessions"] == chat_service.MAX_SESSIONS_PER_PET


def test_여섯_번째를_만들면_가장_오래된_것만_사라진다(
    client: TestClient, store: Store, pets: dict[str, FakePet]
) -> None:
    """**다섯 개는 세션 다섯 개이지 메시지 다섯 개가 아닙니다.**"""
    pet_id = str(pets["mine"].id)
    for i in range(6):
        client.post("/app/chats", json={"pet_id": pet_id, "title": f"대화 {i}"})

    r = client.get("/app/chats", params={"pet_id": pet_id})
    titles = [s["title"] for s in r.json()["sessions"]]
    assert titles == ["대화 5", "대화 4", "대화 3", "대화 2", "대화 1"]
    assert "대화 0" not in titles
    assert len(store.chat_sessions) == 5


def test_밀려나는_것은_그_강아지_것뿐이다(
    client: TestClient, store: Store, pets: dict[str, FakePet]
) -> None:
    """다른 아이의 대화도, 남의 대화도 5개 유지에 걸려서는 안 됩니다."""
    others_first = ChatSession(
        app_user_id=OWNER, pet_id=pets["other"].id, title="다른 아이 대화", agent_categories=[]
    )
    strangers = ChatSession(
        app_user_id=STRANGER, pet_id=pets["stranger"].id, title="남의 대화", agent_categories=[]
    )
    for s in (others_first, strangers):
        s.id = uuid.uuid4()
        store.chat_sessions.append(s)

    for i in range(6):
        client.post("/app/chats", json={"pet_id": str(pets["mine"].id), "title": f"대화 {i}"})

    survivors = {s.title for s in store.chat_sessions}
    assert "다른 아이 대화" in survivors
    assert "남의 대화" in survivors


def test_밀려난_대화의_원문은_같이_사라진다(
    client: TestClient, store: Store, pets: dict[str, FakePet]
) -> None:
    """세션만 지우고 메시지가 남으면 "지웠다"가 거짓말이 됩니다."""
    pet_id = str(pets["mine"].id)
    first = client.post("/app/chats", json={"pet_id": pet_id, "title": "첫 대화"}).json()

    asyncio.run(
        chat_service.append_exchange(
            FakeSession(),
            OWNER,
            uuid.UUID(first["id"]),
            question="배변 훈련 어떻게 해요?",
            response=_answer("training"),
        )
    )
    assert store.chat_messages

    for i in range(5):
        client.post("/app/chats", json={"pet_id": pet_id, "title": f"대화 {i}"})

    assert all(m.session_id != uuid.UUID(first["id"]) for m in store.chat_messages)


def test_저장된_요약은_원본이_밀려나도_남는다(
    client: TestClient, store: Store, pets: dict[str, FakePet]
) -> None:
    """**이 카드의 핵심입니다.** 보관함은 5개 유지의 영향을 받지 않습니다."""
    pet_id = str(pets["mine"].id)
    first = client.post("/app/chats", json={"pet_id": pet_id, "title": "첫 대화"}).json()

    store.chat_summaries.append(
        FakeChatSummary(
            app_user_id=OWNER,
            pet_id=pets["mine"].id,
            session_id=uuid.UUID(first["id"]),
            title="배변 훈련 요약",
            question_summary="배변 훈련을 물었습니다.",
            answer_summary="정해진 자리에서 반복하라고 답했습니다.",
            model="gemini-3.1-flash-lite",
            prompt_version="chat-summary-ko-v1",
            source_message_count=2,
            client_request_id="req-1",
        )
    )

    for i in range(5):
        client.post("/app/chats", json={"pet_id": pet_id, "title": f"대화 {i}"})

    r = client.get("/app/chats/summaries", params={"pet_id": pet_id})
    assert r.status_code == 200, r.text
    saved = r.json()["summaries"]
    assert len(saved) == 1
    assert saved[0]["title"] == "배변 훈련 요약"
    # 원본은 사라졌으므로 연결만 끊깁니다.
    assert saved[0]["session_id"] is None


def test_손으로_지워도_저장된_요약은_남는다(
    client: TestClient, store: Store, pets: dict[str, FakePet]
) -> None:
    pet_id = str(pets["mine"].id)
    made = client.post("/app/chats", json={"pet_id": pet_id, "title": "대화"}).json()
    store.chat_summaries.append(
        FakeChatSummary(
            app_user_id=OWNER,
            pet_id=pets["mine"].id,
            session_id=uuid.UUID(made["id"]),
            title="요약",
            question_summary="질문",
            answer_summary="답",
            model="gemini-3.1-flash-lite",
            prompt_version="chat-summary-ko-v1",
            source_message_count=2,
            client_request_id="req-2",
        )
    )

    assert client.delete(f"/app/chats/{made['id']}").status_code == 204

    r = client.get("/app/chats/summaries", params={"pet_id": pet_id})
    assert len(r.json()["summaries"]) == 1
    assert r.json()["summaries"][0]["session_id"] is None
