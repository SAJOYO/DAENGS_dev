"""services/chat.py — 메시지 적재와 라우팅 메타데이터.

여기서 보는 것은 **무엇을 저장하고 무엇을 저장하지 않는가**입니다. 배지는
라우팅이 낸 것만 쓰고(키워드로 짓지 않고), 답이 안 나온 응답은 답으로 남기지
않으며, 같은 요청이 두 번 와도 두 벌이 쌓이지 않아야 합니다.
"""

import asyncio
import uuid

import pytest

from daengs_backend.models import ChatSession
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    ClarifyRequest,
    OutcomeDetail,
)
from daengs_backend.services import chat as chat_service
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install

OWNER = uuid.uuid4()
PET = uuid.uuid4()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    s.pets.append(FakePet(app_user_id=OWNER, name="네옹", breed="toy_poodle", id=PET))
    return s


@pytest.fixture
def chat_session(store: Store) -> ChatSession:
    created = ChatSession(
        app_user_id=OWNER, pet_id=PET, title="새 대화", agent_categories=[]
    )
    created.id = uuid.uuid4()
    store.chat_sessions.append(created)
    return created


def _ok(capability: str, answer: str = "이렇게 해 보세요.") -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName(capability),
        status=CapabilityStatus.OK,
        data={"answer": answer},
        elapsed_ms=1,
    )


def _response(
    *results: CapabilityResult,
    status: AssistantStatus = AssistantStatus.ANSWERED,
    message: str = "이렇게 해 보세요.",
    clarify: ClarifyRequest | None = None,
) -> AssistantResponse:
    return AssistantResponse(
        request_id=str(uuid.uuid4()),
        status=status,
        message=message,
        results=list(results),
        clarify=clarify,
    )


def _append(session_id: uuid.UUID, response: AssistantResponse, **kw):
    return asyncio.run(
        chat_service.append_exchange(
            FakeSession(),
            OWNER,
            session_id,
            question=kw.pop("question", "배변 훈련 어떻게 해요?"),
            response=response,
            **kw,
        )
    )


# --- 라우팅 메타데이터 --------------------------------------------------------


def test_배지는_라우팅이_낸_능력_이름_그대로다(
    store: Store, chat_session: ChatSession
) -> None:
    """한글 라벨로 접어서 저장하지 않습니다 — 라벨은 앱의 어휘입니다."""
    _append(chat_session.id, _response(_ok("training")))

    answer = next(m for m in store.chat_messages if m.role == "assistant")
    assert answer.agent_categories == ["training"]
    assert chat_session.agent_categories == ["training"]


def test_한_대화에_능력이_여럿이면_배지도_여럿이다(
    store: Store, chat_session: ChatSession
) -> None:
    """**하나로 접지 않습니다** — 접으면 어느 쪽이든 틀린 라벨이 됩니다."""
    _append(chat_session.id, _response(_ok("training"), _ok("life")))

    answer = next(m for m in store.chat_messages if m.role == "assistant")
    assert answer.agent_categories == ["training", "life"]
    assert chat_session.agent_categories == ["training", "life"]


def test_세션_배지는_대화_전체의_합집합이다(
    store: Store, chat_session: ChatSession
) -> None:
    """두 번에 걸쳐 훈련과 생활을 물었으면 카드에 배지가 둘 붙어야 합니다."""
    _append(chat_session.id, _response(_ok("training")))
    _append(chat_session.id, _response(_ok("life")))

    assert chat_session.agent_categories == ["training", "life"]


def test_답을_안_낸_능력은_배지에_안_들어간다(
    store: Store, chat_session: ChatSession
) -> None:
    """기권한 능력에 배지를 붙이면, 카드를 열었을 때 그 능력은 아무 말도 안 했습니다."""
    abstained = CapabilityResult(
        capability=CapabilityName.LIFE,
        status=CapabilityStatus.ABSTAINED,
        abstention=OutcomeDetail(code="no_evidence", message="근거를 찾지 못했습니다."),
        elapsed_ms=1,
    )
    _append(
        chat_session.id,
        _response(_ok("training"), abstained, status=AssistantStatus.PARTIAL),
    )

    answer = next(m for m in store.chat_messages if m.role == "assistant")
    assert answer.agent_categories == ["training"]


# --- 실패한 응답 --------------------------------------------------------------


def test_실패한_응답은_답으로_저장하지_않는다(
    store: Store, chat_session: ChatSession
) -> None:
    """**질문만 남습니다.** 실패를 완료된 답처럼 남기면 나중에 그것을 답으로 읽습니다."""
    failed = _response(
        status=AssistantStatus.FAILED, message="처리하지 못했습니다."
    )
    _append(chat_session.id, failed)

    roles = [m.role for m in store.chat_messages]
    assert roles == ["user"]


def test_되물음도_답으로_저장하지_않는다(
    store: Store, chat_session: ChatSession
) -> None:
    """CLARIFY 는 아직 답이 아닙니다 — 클라이언트가 채워서 다시 묻습니다 (D-034)."""
    clarify = _response(
        status=AssistantStatus.CLARIFY,
        message="어느 아이인가요?",
        clarify=ClarifyRequest(question="어느 아이인가요?", missing=["pet"]),
    )
    _append(chat_session.id, clarify)

    assert [m.role for m in store.chat_messages] == ["user"]


def test_거절은_답으로_저장한다(store: Store, chat_session: ChatSession) -> None:
    """REFUSED 는 실패가 아니라 **의도된 답**입니다 (D-033). 사용자는 그것을 봤습니다."""
    refused = CapabilityResult(
        capability=CapabilityName.TRAINING,
        status=CapabilityStatus.REFUSED,
        refusal=OutcomeDetail(code="MEDICAL_REFUSAL", message="수의사와 상의하세요."),
        elapsed_ms=1,
    )
    _append(
        chat_session.id,
        _response(refused, status=AssistantStatus.REFUSED, message="수의사와 상의하세요."),
    )

    answer = [m for m in store.chat_messages if m.role == "assistant"]
    assert len(answer) == 1
    assert answer[0].assistant_status == "REFUSED"
    # 거절한 능력은 답을 낸 것이 아니므로 배지는 비어 있습니다.
    assert answer[0].agent_categories == []


# --- 멱등 --------------------------------------------------------------------


def test_같은_멱등_키로_두_번_보내도_한_벌만_쌓인다(
    store: Store, chat_session: ChatSession
) -> None:
    """두 번 눌렀거나 네트워크가 재시도한 것입니다."""
    _append(chat_session.id, _response(_ok("training")), client_message_id="msg-1")
    _append(chat_session.id, _response(_ok("training")), client_message_id="msg-1")

    assert len(store.chat_messages) == 2  # 질문 하나 + 답 하나


def test_멱등_키가_다르면_이어서_쌓인다(
    store: Store, chat_session: ChatSession
) -> None:
    _append(chat_session.id, _response(_ok("training")), client_message_id="msg-1")
    _append(chat_session.id, _response(_ok("life")), client_message_id="msg-2")

    assert len(store.chat_messages) == 4


def test_멱등_재시도는_그때_저장된_것을_돌려준다(
    store: Store, chat_session: ChatSession
) -> None:
    first = _append(
        chat_session.id, _response(_ok("training")), client_message_id="msg-1"
    )
    again = _append(
        chat_session.id, _response(_ok("life")), client_message_id="msg-1"
    )

    # 두 번째 호출의 응답(life)은 무시되고, 처음 저장된 것이 그대로 나옵니다.
    assert [m.role for m in again] == [m.role for m in first]
    assert all("life" not in m.agent_categories for m in store.chat_messages)


# --- 제목 --------------------------------------------------------------------


def test_첫_질문이_카드_제목이_된다(store: Store, chat_session: ChatSession) -> None:
    """**LLM 을 부르지 않습니다** — 제목 때문에 대화마다 생성 비용을 물 이유가 없습니다."""
    _append(chat_session.id, _response(_ok("training")), question="배변 훈련 어떻게 해요?")
    assert chat_session.title == "배변 훈련 어떻게 해요?"


def test_긴_질문은_잘리고_잘린_티가_난다() -> None:
    long_question = "가" * 200
    title = chat_service.build_title(long_question)
    assert len(title) == 120
    assert title.endswith("…")


def test_줄바꿈은_한_줄로_접힌다() -> None:
    """카드가 한 줄로 보여 주므로 공백으로 접습니다."""
    assert chat_service.build_title("배변 훈련\n어떻게\t해요?") == "배변 훈련 어떻게 해요?"
