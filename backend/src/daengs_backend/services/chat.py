"""Short-transaction rules for product chat persistence (D-043).

External orchestration/Gemini calls are intentionally absent from turn transactions. Summary
generation uses reserve TX -> close session -> external call -> completion TX.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from daengs_backend.models import ChatSession, ChatSummary, ChatTurn
from daengs_backend.orchestration.contracts import AssistantResponse, CapabilityStatus
from daengs_backend.repositories import chat as chat_repo
from daengs_backend.services.chat_summary import (
    PROMPT_VERSION,
    SUMMARY_MODEL_ID,
    ChatSummaryDraft,
    ChatSummaryError,
    GeminiChatSummarizer,
    render_transcript,
)

MAX_SESSIONS_PER_PET = 5
MAX_QUESTION_CHARS = 2_000
MAX_ASSISTANT_CHARS = 8_000
MAX_COMPLETED_TURNS = 30
MAX_TRANSCRIPT_CHARS = 320_000
STALE_PROCESSING_AFTER = timedelta(minutes=5)
_TITLE_MAX = 120


class ChatSessionNotFoundError(Exception):
    pass


class ChatTurnNotFoundError(Exception):
    pass


class PetNotOwnedError(Exception):
    pass


class EmptyConversationError(Exception):
    pass


class ContentLimitError(Exception):
    def __init__(self, field: str, limit: int) -> None:
        super().__init__(f"{field} exceeds {limit} characters")
        self.field = field
        self.limit = limit


class TurnLimitError(Exception):
    pass


class TranscriptLimitError(Exception):
    pass


class CompletionConflictError(Exception):
    pass


class ExistingSummaryError(Exception):
    """The same successfully summarized source state already exists."""

    def __init__(self, summary_id: uuid.UUID) -> None:
        super().__init__(f"summary already exists: {summary_id}")
        self.summary_id = summary_id


class SummaryProcessingError(Exception):
    def __init__(self, summary_id: uuid.UUID) -> None:
        super().__init__(f"summary is already processing: {summary_id}")
        self.summary_id = summary_id


class SummaryRequestConflictError(Exception):
    pass


@dataclass(frozen=True)
class SummaryReservation:
    summary_id: uuid.UUID
    transcript: str


def build_title(first_message: str) -> str:
    flattened = " ".join(first_message.split())
    if not flattened:
        return "새 대화"
    if len(flattened) <= _TITLE_MAX:
        return flattened
    return flattened[: _TITLE_MAX - 1] + "…"


def categories_of(response: AssistantResponse) -> list[str]:
    categories: list[str] = []
    for result in response.results:
        if result.status is CapabilityStatus.OK and result.capability.value not in categories:
            categories.append(result.capability.value)
    return categories


def public_response_of(response: AssistantResponse) -> dict[str, object]:
    """Only the already-public response contract; no prompts, exceptions, or provider payloads."""
    return response.model_dump(mode="json")


async def _require_owned_pet(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> None:
    if await chat_repo.get_owned_pet_id(session, app_user_id, pet_id) is None:
        raise PetNotOwnedError


async def _require_owned_session(
    session: AsyncSession, app_user_id: uuid.UUID, session_id: uuid.UUID
) -> ChatSession:
    found = await chat_repo.get_owned_session(session, app_user_id, session_id)
    if found is None:
        raise ChatSessionNotFoundError
    return found


async def list_sessions(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[ChatSession]:
    await _require_owned_pet(session, app_user_id, pet_id)
    return await chat_repo.list_active_sessions(
        session, app_user_id, pet_id, MAX_SESSIONS_PER_PET
    )


async def create_session(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    title: str | None = None,
) -> ChatSession:
    """Return the one draft for this owner/pet, creating it without a pet row lock."""
    await _require_owned_pet(session, app_user_id, pet_id)
    existing = await chat_repo.get_draft(session, app_user_id, pet_id)
    if existing is not None:
        return existing

    draft = chat_repo.add_session(
        session,
        ChatSession(
            app_user_id=app_user_id,
            pet_id=pet_id,
            title=build_title(title or ""),
            agent_categories=[],
            last_message_at=None,
        ),
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await chat_repo.get_draft(session, app_user_id, pet_id)
        if winner is None:
            raise
        return winner
    return draft


async def get_session_with_turns(
    session: AsyncSession, app_user_id: uuid.UUID, session_id: uuid.UUID
) -> tuple[ChatSession, list[ChatTurn]]:
    chat_session = await _require_owned_session(session, app_user_id, session_id)
    return chat_session, await chat_repo.list_turns(session, session_id)


async def delete_session(
    session: AsyncSession, app_user_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    chat_session = await _require_owned_session(session, app_user_id, session_id)
    await chat_repo.delete_session(session, chat_session)
    await session.commit()


def _validate_question(question: str) -> None:
    if not question:
        raise ContentLimitError("question", MAX_QUESTION_CHARS)
    if len(question) > MAX_QUESTION_CHARS:
        raise ContentLimitError("question", MAX_QUESTION_CHARS)


async def reserve_turn(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    client_message_id: uuid.UUID,
    question: str,
) -> ChatTurn:
    """Reserve a turn and commit before any orchestrator call."""
    _validate_question(question)
    chat_session = await chat_repo.get_owned_session_for_update(
        session, app_user_id, session_id
    )
    if chat_session is None:
        raise ChatSessionNotFoundError

    existing = await chat_repo.get_turn_by_client_id(
        session, session_id, client_message_id
    )
    if existing is not None:
        return existing
    if await chat_repo.count_reserved_turns(session, session_id) >= MAX_COMPLETED_TURNS:
        raise TurnLimitError

    completed = await chat_repo.list_turns(session, session_id, completed_only=True)
    projected = _transcript_char_count(completed) + len(question)
    if projected > MAX_TRANSCRIPT_CHARS:
        raise TranscriptLimitError

    turn = chat_repo.add_turn(
        session,
        ChatTurn(
            session_id=session_id,
            client_message_id=client_message_id,
            processing_status="processing",
            user_content=question,
            agent_categories=[],
        ),
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await chat_repo.get_turn_by_client_id(
            session, session_id, client_message_id
        )
        if winner is None:
            raise
        return winner
    return turn


async def complete_turn(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    turn_id: uuid.UUID,
    *,
    response: AssistantResponse,
) -> ChatTurn:
    """Conditionally complete a reserved turn and activate/prune in one short TX."""
    owned = await chat_repo.get_owned_turn(session, app_user_id, turn_id)
    if owned is None:
        raise ChatTurnNotFoundError
    turn, chat_session = owned
    if turn.processing_status != "processing":
        raise CompletionConflictError
    if not response.message or len(response.message) > MAX_ASSISTANT_CHARS:
        await chat_repo.fail_turn_if_processing(
            session, turn_id, error_code="ASSISTANT_CONTENT_TOO_LONG"
        )
        await session.commit()
        raise ContentLimitError("assistant", MAX_ASSISTANT_CHARS)

    completed = await chat_repo.list_turns(
        session, chat_session.id, completed_only=True
    )
    if _transcript_char_count(completed) + len(turn.user_content) + len(
        response.message
    ) > MAX_TRANSCRIPT_CHARS:
        await chat_repo.fail_turn_if_processing(
            session, turn_id, error_code="TRANSCRIPT_TOO_LONG"
        )
        await session.commit()
        raise TranscriptLimitError

    first_activation = chat_session.last_message_at is None
    if first_activation:
        if (
            await chat_repo.lock_owned_pet(
                session, chat_session.app_user_id, chat_session.pet_id
            )
            is None
        ):
            raise PetNotOwnedError
        refreshed = await chat_repo.get_owned_session_for_update(
            session, app_user_id, chat_session.id
        )
        if refreshed is None:
            raise ChatSessionNotFoundError
        chat_session = refreshed
        first_activation = chat_session.last_message_at is None

    categories = categories_of(response)
    stored = await chat_repo.complete_turn_if_processing(
        session,
        turn_id,
        assistant_content=response.message,
        assistant_status=response.status.value,
        request_id=response.request_id,
        agent_categories=categories,
        public_response=public_response_of(response),
    )
    if stored is None:
        await session.rollback()
        raise CompletionConflictError

    merged = list(chat_session.agent_categories)
    for category in categories:
        if category not in merged:
            merged.append(category)
    chat_repo.touch_active_session(session, chat_session, merged)
    if chat_session.title == "새 대화":
        chat_session.title = build_title(turn.user_content)

    if first_activation:
        for stale in await chat_repo.oldest_active_sessions_beyond(
            session,
            chat_session.app_user_id,
            chat_session.pet_id,
            MAX_SESSIONS_PER_PET,
        ):
            await chat_repo.delete_session(session, stale)

    await session.commit()
    return stored


async def fail_turn(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    turn_id: uuid.UUID,
    *,
    error_code: str,
) -> ChatTurn:
    if await chat_repo.get_owned_turn(session, app_user_id, turn_id) is None:
        raise ChatTurnNotFoundError
    failed = await chat_repo.fail_turn_if_processing(
        session, turn_id, error_code=error_code
    )
    if failed is None:
        raise CompletionConflictError
    await session.commit()
    return failed


def _transcript_char_count(turns: list[ChatTurn]) -> int:
    return sum(len(turn.user_content) + len(turn.assistant_content or "") for turn in turns)


def _render_completed_turns(turns: list[ChatTurn]) -> str:
    messages: list[tuple[str, str]] = []
    for turn in turns:
        messages.append(("user", turn.user_content))
        messages.append(("assistant", turn.assistant_content or ""))
    return render_transcript(messages)


def _raise_for_existing_summary(summary: ChatSummary) -> None:
    if summary.processing_status == "completed":
        raise ExistingSummaryError(summary.id)
    if summary.processing_status == "processing":
        raise SummaryProcessingError(summary.id)
    raise SummaryRequestConflictError


async def reserve_summary(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    client_request_id: uuid.UUID,
    now: datetime | None = None,
) -> SummaryReservation:
    """Reserve and commit. The caller must close this AsyncSession before Gemini."""
    chat_session = await _require_owned_session(session, app_user_id, session_id)
    turns = await chat_repo.list_turns(session, session_id, completed_only=True)
    if not turns:
        raise EmptyConversationError
    if len(turns) > MAX_COMPLETED_TURNS:
        raise TurnLimitError
    transcript = _render_completed_turns(turns)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        raise TranscriptLimitError

    timestamp = now or datetime.now(UTC)
    stale_count = await chat_repo.fail_stale_summaries(
        session,
        source_session_id=session_id,
        cutoff=timestamp - STALE_PROCESSING_AFTER,
    )

    by_request = await chat_repo.summary_by_request_id(
        session, app_user_id, client_request_id
    )
    if by_request is not None:
        if stale_count:
            await session.commit()
        _raise_for_existing_summary(by_request)

    source_count = len(turns)
    existing = await chat_repo.active_summary_for_source(
        session, session_id, source_count
    )
    if existing is not None:
        _raise_for_existing_summary(existing)

    summary = chat_repo.add_summary(
        session,
        ChatSummary(
            app_user_id=app_user_id,
            pet_id=chat_session.pet_id,
            source_session_id=session_id,
            source_turn_count=source_count,
            client_request_id=client_request_id,
            processing_status="processing",
            agent_categories=list(chat_session.agent_categories),
            processing_started_at=timestamp,
        ),
    )
    try:
        await session.flush()
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await chat_repo.active_summary_for_source(
            session, session_id, source_count
        )
        if winner is not None:
            _raise_for_existing_summary(winner)
        by_request = await chat_repo.summary_by_request_id(
            session, app_user_id, client_request_id
        )
        if by_request is not None:
            _raise_for_existing_summary(by_request)
        raise
    return SummaryReservation(summary_id=summary.id, transcript=transcript)


async def complete_summary(
    session: AsyncSession, summary_id: uuid.UUID, draft: ChatSummaryDraft
) -> ChatSummary:
    citations = [citation.model_dump(mode="json") for citation in draft.source_citations]
    completed = await chat_repo.complete_summary_if_processing(
        session,
        summary_id,
        title=draft.title,
        question_summary=draft.question_summary,
        answer_summary=draft.answer_summary,
        key_points=draft.key_points,
        cautions=draft.cautions,
        source_citations=citations,
        model=SUMMARY_MODEL_ID,
        prompt_version=PROMPT_VERSION,
    )
    if completed is None:
        await session.rollback()
        raise CompletionConflictError
    await session.commit()
    return completed


async def fail_summary(
    session: AsyncSession, summary_id: uuid.UUID, *, error_code: str
) -> None:
    failed = await chat_repo.fail_summary_if_processing(
        session, summary_id, error_code=error_code
    )
    if failed is not None:
        await session.commit()
    else:
        await session.rollback()


async def create_summary(
    session_factory: async_sessionmaker[AsyncSession],
    app_user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    client_request_id: uuid.UUID,
    summarizer: GeminiChatSummarizer | None = None,
) -> ChatSummary:
    """Full workflow with no AsyncSession alive across the external call."""
    async with session_factory() as reserve_session:
        reservation = await reserve_summary(
            reserve_session,
            app_user_id,
            session_id,
            client_request_id=client_request_id,
        )

    try:
        draft = await (summarizer or GeminiChatSummarizer()).summarize(
            transcript=reservation.transcript
        )
    except ChatSummaryError:
        async with session_factory() as failure_session:
            await fail_summary(
                failure_session,
                reservation.summary_id,
                error_code="SUMMARY_GENERATION_FAILED",
            )
        raise

    async with session_factory() as completion_session:
        return await complete_summary(completion_session, reservation.summary_id, draft)


async def list_summaries(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[ChatSummary]:
    await _require_owned_pet(session, app_user_id, pet_id)
    return await chat_repo.list_completed_summaries(session, app_user_id, pet_id)


__all__ = [
    "MAX_ASSISTANT_CHARS",
    "MAX_COMPLETED_TURNS",
    "MAX_QUESTION_CHARS",
    "MAX_SESSIONS_PER_PET",
    "MAX_TRANSCRIPT_CHARS",
    "STALE_PROCESSING_AFTER",
    "ChatSessionNotFoundError",
    "ChatTurnNotFoundError",
    "CompletionConflictError",
    "ContentLimitError",
    "EmptyConversationError",
    "ExistingSummaryError",
    "PetNotOwnedError",
    "SummaryProcessingError",
    "SummaryRequestConflictError",
    "SummaryReservation",
    "TranscriptLimitError",
    "TurnLimitError",
    "build_title",
    "categories_of",
    "complete_summary",
    "complete_turn",
    "create_session",
    "create_summary",
    "delete_session",
    "fail_summary",
    "fail_turn",
    "get_session_with_turns",
    "list_sessions",
    "list_summaries",
    "public_response_of",
    "reserve_summary",
    "reserve_turn",
]
