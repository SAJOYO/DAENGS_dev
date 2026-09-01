"""services/chat_summary.py + services/chat.py 의 요약 저장.

**실제 API 키가 필요 없습니다.** `GeminiChatSummarizer(generate=...)` 에 가짜를
주입하므로 유료 호출이 일어나지 않습니다 — `test_orchestration_semantic_router.py`
가 라우터에 하는 것과 같은 방식입니다.

여기서 보는 것은 세 가지입니다: 요약이 **이 대화만** 보는가, 모델이 스키마를 어기면
저장이 막히는가, 두 번 눌러도 한 건만 남는가.
"""

import asyncio
import json
import uuid

import pytest

from daengs_backend.models import ChatMessage, ChatSession
from daengs_backend.services import chat as chat_service
from daengs_backend.services.chat_summary import (
    PROMPT_VERSION,
    SUMMARY_MODEL_ID,
    ChatSummaryDraft,
    ChatSummaryError,
    GeminiChatSummarizer,
    build_summary_prompt,
    render_transcript,
    validate_summary_draft,
)
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install

OWNER = uuid.uuid4()
PET = uuid.uuid4()

_GOOD = {
    "title": "배변 훈련 요약",
    "question_summary": "배변 훈련 방법을 물었습니다.",
    "answer_summary": "정해진 자리에서 반복하라고 답했습니다.",
    "key_points": ["같은 자리를 쓴다"],
    "cautions": ["설사가 계속되면 병원에 가세요"],
    "source_citations": ["동물보호법 제8조"],
}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    s.pets.append(FakePet(app_user_id=OWNER, name="네옹", breed="toy_poodle", id=PET))
    return s


@pytest.fixture
def conversation(store: Store) -> ChatSession:
    """질문 하나와 답 하나가 든 대화."""
    session = ChatSession(
        app_user_id=OWNER, pet_id=PET, title="배변 훈련", agent_categories=["training"]
    )
    session.id = uuid.uuid4()
    store.chat_sessions.append(session)

    for role, content in (
        ("user", "배변 훈련 어떻게 해요?"),
        ("assistant", "정해진 자리에서 반복하세요. 설사가 계속되면 병원에 가세요."),
    ):
        message = ChatMessage(
            session_id=session.id, role=role, content=content, agent_categories=[]
        )
        message.id = uuid.uuid4()
        message.created_at = store.tick()
        store.chat_messages.append(message)
    return session


def _summarizer(payload: object) -> GeminiChatSummarizer:
    """공급자 대역. **네트워크로 나가지 않습니다.**"""

    async def fake_generate(prompt: str) -> object:
        fake_generate.seen.append(prompt)
        return payload

    fake_generate.seen = []  # type: ignore[attr-defined]
    summarizer = GeminiChatSummarizer(generate=fake_generate)
    summarizer.seen = fake_generate.seen  # type: ignore[attr-defined]
    return summarizer


def _create(session_id: uuid.UUID, summarizer: GeminiChatSummarizer, key: str = "req-1"):
    return asyncio.run(
        chat_service.create_summary(
            FakeSession(),
            OWNER,
            session_id,
            client_request_id=key,
            summarizer=summarizer,
        )
    )


# --- 요약 대상 ----------------------------------------------------------------


def test_요약은_그_대화만_본다(store: Store, conversation: ChatSession) -> None:
    """**다른 대화가 프롬프트에 섞이면 안 됩니다.**"""
    other = ChatSession(
        app_user_id=OWNER, pet_id=PET, title="다른 대화", agent_categories=[]
    )
    other.id = uuid.uuid4()
    store.chat_sessions.append(other)
    stray = ChatMessage(
        session_id=other.id,
        role="user",
        content="산책 가도 되나요?",
        agent_categories=[],
    )
    stray.id = uuid.uuid4()
    store.chat_messages.append(stray)

    summarizer = _summarizer(_GOOD)
    _create(conversation.id, summarizer)

    prompt = summarizer.seen[0]  # type: ignore[attr-defined]
    assert "배변 훈련 어떻게 해요?" in prompt
    assert "산책 가도 되나요?" not in prompt


def test_빈_대화는_모델을_부르지_않는다(store: Store) -> None:
    """빈 대화를 보내면 모델이 **없는 대화를 지어냅니다.** 부르기 전에 막습니다."""
    empty = ChatSession(
        app_user_id=OWNER, pet_id=PET, title="빈 대화", agent_categories=[]
    )
    empty.id = uuid.uuid4()
    store.chat_sessions.append(empty)

    summarizer = _summarizer(_GOOD)
    with pytest.raises(chat_service.EmptyConversationError):
        _create(empty.id, summarizer)
    assert summarizer.seen == []  # type: ignore[attr-defined]


def test_남의_대화는_요약하지_못한다(store: Store) -> None:
    stranger_session = ChatSession(
        app_user_id=uuid.uuid4(), pet_id=uuid.uuid4(), title="남의 대화", agent_categories=[]
    )
    stranger_session.id = uuid.uuid4()
    store.chat_sessions.append(stranger_session)

    with pytest.raises(chat_service.ChatSessionNotFoundError):
        _create(stranger_session.id, _summarizer(_GOOD))


# --- 프롬프트의 약속 ----------------------------------------------------------


def test_프롬프트가_새_상담을_금지한다() -> None:
    """요약기는 상담기가 아닙니다 — 그 금지가 프롬프트에 남아 있어야 합니다."""
    prompt = build_summary_prompt(transcript="[USER] 안녕")
    assert "Do not add facts" in prompt
    assert "Do not answer the user's question" in prompt
    assert "Preserve every warning" in prompt
    assert PROMPT_VERSION in prompt


def test_빈_대화로는_프롬프트를_만들_수_없다() -> None:
    with pytest.raises(ValueError):
        build_summary_prompt(transcript="   ")


def test_대화는_역할_표시와_함께_넘어간다() -> None:
    rendered = render_transcript([("user", "질문"), ("assistant", "답")])
    assert rendered == "[USER] 질문\n[ASSISTANT] 답"


# --- 스키마 검증 --------------------------------------------------------------


def test_스키마를_어긴_출력은_거부된다() -> None:
    """`validate_summary_draft` 는 **잘못된 원출력을 노출하지 않습니다** (None 만 돌려줍니다)."""
    assert validate_summary_draft("이건 JSON 이 아닙니다") is None
    assert validate_summary_draft({"title": "제목만 있음"}) is None
    assert validate_summary_draft({**_GOOD, "몰래": "낀 필드"}) is None
    assert validate_summary_draft(None) is None


def test_올바른_출력은_문자열이든_객체든_통과한다() -> None:
    assert validate_summary_draft(_GOOD) is not None
    assert validate_summary_draft(json.dumps(_GOOD, ensure_ascii=False)) is not None
    assert validate_summary_draft(ChatSummaryDraft.model_validate(_GOOD)) is not None


def test_주의가_비어_있어도_통과한다() -> None:
    """원문에 주의가 없었으면 **지어내지 않는 것**이 맞습니다."""
    draft = validate_summary_draft({**_GOOD, "cautions": []})
    assert draft is not None
    assert draft.cautions == []


def test_스키마를_두_번_어기면_저장하지_않는다(
    store: Store, conversation: ChatSession
) -> None:
    """**부분 저장을 하지 않습니다** — 빈 껍데기가 보관함에 남으면 성공으로 보입니다."""
    summarizer = _summarizer({"title": "쓰레기"})
    with pytest.raises(ChatSummaryError):
        _create(conversation.id, summarizer)

    assert store.chat_summaries == []
    # O-14 와 같은 규칙 — 한 번만 다시 시도합니다.
    assert len(summarizer.seen) == 2  # type: ignore[attr-defined]


def test_공급자가_터지면_요약_실패로_바뀐다(
    store: Store, conversation: ChatSession
) -> None:
    async def boom(prompt: str) -> object:
        raise RuntimeError("provider down")

    with pytest.raises(ChatSummaryError):
        _create(conversation.id, GeminiChatSummarizer(generate=boom))
    assert store.chat_summaries == []


# --- 저장 --------------------------------------------------------------------


def test_요약은_구조화되어_저장된다(store: Store, conversation: ChatSession) -> None:
    saved = _create(conversation.id, _summarizer(_GOOD))

    assert saved.title == "배변 훈련 요약"
    assert saved.key_points == ["같은 자리를 쓴다"]
    assert saved.cautions == ["설사가 계속되면 병원에 가세요"]
    assert saved.source_citations == ["동물보호법 제8조"]
    assert saved.source_message_count == 2
    # 무엇으로 만들었는지 남습니다 — 프롬프트를 고쳤을 때 옛 요약과 가릅니다.
    assert saved.model == SUMMARY_MODEL_ID
    assert saved.prompt_version == PROMPT_VERSION


def test_요약의_배지는_원본_세션의_것을_물려받는다(
    store: Store, conversation: ChatSession
) -> None:
    """능력 이름을 **요약 모델에게 고르게 하지 않습니다** — 라우팅이 이미 정한 사실입니다."""
    saved = _create(conversation.id, _summarizer(_GOOD))
    assert saved.agent_categories == ["training"]


def test_같은_키로_두_번_누르면_모델을_안_부른다(
    store: Store, conversation: ChatSession
) -> None:
    """요약은 유료 호출이라 메시지보다 중복이 비쌉니다."""
    first = _create(conversation.id, _summarizer(_GOOD), key="req-1")

    second_summarizer = _summarizer(_GOOD)
    second = _create(conversation.id, second_summarizer, key="req-1")

    assert second.id == first.id
    assert len(store.chat_summaries) == 1
    assert second_summarizer.seen == []  # type: ignore[attr-defined]


def test_키가_다르면_다시_요약한다(store: Store, conversation: ChatSession) -> None:
    """대화가 이어진 뒤 다시 저장하는 것은 막지 않습니다."""
    _create(conversation.id, _summarizer(_GOOD), key="req-1")
    _create(conversation.id, _summarizer(_GOOD), key="req-2")
    assert len(store.chat_summaries) == 2
