"""Short-transaction rules for product chat persistence (D-048).

External orchestration/Gemini calls are intentionally absent from turn transactions. Both a
persisted ``/assistant/query`` turn and summary generation use
reserve TX -> close session -> external call -> completion TX (``run_persisted_turn``,
``create_summary``).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from daengs_backend.models import ChatSession, ChatSummary, ChatTurn
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityStatus,
)
from daengs_backend.repositories import app_user as app_user_repo
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
MAX_FAILED_TURNS = 30
MAX_TRANSCRIPT_CHARS = 320_000
STALE_PROCESSING_AFTER = timedelta(minutes=5)
_TITLE_MAX = 120


class ChatSessionNotFoundError(Exception):
    pass


class ChatTurnNotFoundError(Exception):
    pass


class PetNotOwnedError(Exception):
    pass


class AppUserNotActiveError(Exception):
    """The token is still valid but the member is withdrawn or otherwise not active."""


class ActiveDogMismatchError(Exception):
    """The request's ``active_dog_id`` names a different dog than the conversation's pet.

    The conversation is the durable record; a routing hint cannot re-home it.
    """

    def __init__(self, session_pet_id: uuid.UUID) -> None:
        super().__init__(f"active_dog_id differs from the session pet {session_pet_id}")
        self.session_pet_id = session_pet_id


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


class TurnIdempotencyConflictError(Exception):
    """The client reused a ``client_message_id`` for a *different* question.

    Never merged silently: answering the stored question would look like a correct reply to
    the new one, and overwriting the stored one would rewrite history the user already saw.
    """

    def __init__(self, turn_id: uuid.UUID) -> None:
        super().__init__(f"client_message_id reused with different content: {turn_id}")
        self.turn_id = turn_id


class TurnProcessingError(Exception):
    """The same reservation is still being answered; the client must wait, not retry."""

    def __init__(self, turn_id: uuid.UUID) -> None:
        super().__init__(f"turn is already processing: {turn_id}")
        self.turn_id = turn_id


class TurnFailedError(Exception):
    """The retained UUID belongs to a failed turn. Retry with a fresh one."""

    def __init__(self, turn_id: uuid.UUID, error_code: str) -> None:
        super().__init__(f"turn already failed ({error_code}): {turn_id}")
        self.turn_id = turn_id
        self.error_code = error_code


class TurnPersistenceError(Exception):
    """A generated non-failure response could not be committed as a completed turn."""

    def __init__(self, turn_id: uuid.UUID, persistence_error_code: str) -> None:
        super().__init__(f"turn persistence failed ({persistence_error_code}): {turn_id}")
        self.turn_id = turn_id
        self.persistence_error_code = persistence_error_code
        self.retry_with_fresh_client_message_id = True


class ChatSummaryNotFoundError(Exception):
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


def _resolve_existing_turn(existing: ChatTurn, question: str) -> ChatTurn:
    """Decide what a reused ``client_message_id`` means.

    Only an exact replay of a completed turn is returned, so the caller can answer from the
    stored ``public_response`` without calling the orchestrator. Everything else raises: a
    different question is a client bug, a live reservation is still being answered, and a
    retained failed or stale rows stay burned. Once bounded retention prunes a failed row,
    that UUID no longer has a reservation to conflict with.
    """
    if existing.user_content != question:
        raise TurnIdempotencyConflictError(existing.id)
    if existing.processing_status == "completed":
        return existing
    if existing.processing_status == "processing":
        raise TurnProcessingError(existing.id)
    raise TurnFailedError(existing.id, existing.error_code or "FAILED")


async def reserve_turn(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    client_message_id: uuid.UUID,
    question: str,
    now: datetime | None = None,
) -> ChatTurn:
    """Reserve a turn and commit before any orchestrator call.

    Returns the new ``processing`` row, or the existing ``completed`` row for an exact replay
    (same UUID, same question). See ``_resolve_existing_turn`` for every other reuse.
    """
    _validate_question(question)
    chat_session = await chat_repo.get_owned_session_for_update(
        session, app_user_id, session_id
    )
    if chat_session is None:
        raise ChatSessionNotFoundError

    # Stale recovery and failed-row retention happen under the session lock. Commit them on
    # their own so cleanup survives whatever reservation check below raises, then relock.
    cutoff = (now or datetime.now(UTC)) - STALE_PROCESSING_AFTER
    stale_count = await chat_repo.fail_stale_turns(
        session, session_id=session_id, cutoff=cutoff
    )
    pruned_count = await chat_repo.prune_failed_turns(
        session, session_id=session_id, keep=MAX_FAILED_TURNS
    )
    if stale_count or pruned_count:
        await session.commit()
        chat_session = await chat_repo.get_owned_session_for_update(
            session, app_user_id, session_id
        )
        if chat_session is None:
            raise ChatSessionNotFoundError

    existing = await chat_repo.get_turn_by_client_id(
        session, session_id, client_message_id
    )
    if existing is not None:
        return _resolve_existing_turn(existing, question)
    capacity_turns = await chat_repo.list_capacity_turns(session, session_id)
    if len(capacity_turns) >= MAX_COMPLETED_TURNS:
        raise TurnLimitError

    projected = _reserved_transcript_char_count(capacity_turns)
    projected += len(question) + MAX_ASSISTANT_CHARS
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
        return _resolve_existing_turn(winner, question)
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

    # Every completion updates session aggregates (last_message_at and the category
    # union) and checks the shared transcript limit. Serialize those short writes per
    # session; otherwise two different turns can both pass an outdated size check and
    # overwrite each other's category update. populate_existing in the repository also
    # refreshes a row that waited behind another completion.
    locked_session = await chat_repo.get_owned_session_for_update(
        session, app_user_id, chat_session.id
    )
    if locked_session is None:
        raise ChatSessionNotFoundError
    chat_session = locked_session

    if turn.processing_status != "processing":
        raise CompletionConflictError
    if not response.message or len(response.message) > MAX_ASSISTANT_CHARS:
        failed = await chat_repo.fail_turn_if_processing(
            session, turn_id, error_code="ASSISTANT_CONTENT_TOO_LONG"
        )
        if failed is None:
            await session.rollback()
            raise CompletionConflictError
        await chat_repo.prune_failed_turns(
            session, session_id=chat_session.id, keep=MAX_FAILED_TURNS
        )
        await session.commit()
        raise ContentLimitError("assistant", MAX_ASSISTANT_CHARS)

    completed = await chat_repo.list_turns(
        session, chat_session.id, completed_only=True
    )
    if _transcript_char_count(completed) + len(turn.user_content) + len(
        response.message
    ) > MAX_TRANSCRIPT_CHARS:
        failed = await chat_repo.fail_turn_if_processing(
            session, turn_id, error_code="TRANSCRIPT_TOO_LONG"
        )
        if failed is None:
            await session.rollback()
            raise CompletionConflictError
        await chat_repo.prune_failed_turns(
            session, session_id=chat_session.id, keep=MAX_FAILED_TURNS
        )
        await session.commit()
        raise TranscriptLimitError

    first_activation = chat_session.last_message_at is None
    if first_activation and (
        await chat_repo.lock_owned_pet(
            session, chat_session.app_user_id, chat_session.pet_id
        )
        is None
    ):
        raise PetNotOwnedError

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
    owned = await chat_repo.get_owned_turn(session, app_user_id, turn_id)
    if owned is None:
        raise ChatTurnNotFoundError
    turn, chat_session = owned
    if await chat_repo.get_owned_session_for_update(
        session, app_user_id, chat_session.id
    ) is None:
        raise ChatSessionNotFoundError
    if turn.processing_status != "processing":
        raise CompletionConflictError
    failed = await chat_repo.fail_turn_if_processing(
        session, turn_id, error_code=error_code
    )
    if failed is None:
        raise CompletionConflictError
    await chat_repo.prune_failed_turns(
        session, session_id=chat_session.id, keep=MAX_FAILED_TURNS
    )
    await session.commit()
    return failed


def _transcript_char_count(turns: list[ChatTurn]) -> int:
    return sum(len(turn.user_content) + len(turn.assistant_content or "") for turn in turns)


def _reserved_transcript_char_count(turns: list[ChatTurn]) -> int:
    """Committed content plus worst-case answers for every live reservation."""
    return sum(
        len(turn.user_content)
        + (
            len(turn.assistant_content or "")
            if turn.processing_status == "completed"
            else MAX_ASSISTANT_CHARS
        )
        for turn in turns
    )


#: The external call. Receives the session's pet id (as the ``active_dog_id`` the
#: orchestrator should see) and returns what the user will be shown.
Orchestrate = Callable[[str], Awaitable[AssistantResponse]]

#: Expected completion-stage failures that must become an explicit API error. A generated
#: non-FAILED response is never returned as HTTP 200 unless its identical public response
#: has been committed in a completed turn.
_PERSISTENCE_FAILURES = (
    ContentLimitError,
    TranscriptLimitError,
    CompletionConflictError,
    ChatTurnNotFoundError,
    ChatSessionNotFoundError,
    PetNotOwnedError,
)


def _persistence_error_code(error: Exception) -> str:
    if isinstance(error, ContentLimitError):
        return "ASSISTANT_CONTENT_TOO_LONG"
    if isinstance(error, TranscriptLimitError):
        return "TRANSCRIPT_TOO_LONG"
    if isinstance(error, CompletionConflictError):
        return "COMPLETION_CONFLICT"
    if isinstance(error, ChatTurnNotFoundError):
        return "TURN_NOT_FOUND"
    if isinstance(error, ChatSessionNotFoundError):
        return "SESSION_NOT_FOUND"
    if isinstance(error, PetNotOwnedError):
        return "PET_NOT_OWNED"
    raise TypeError(f"unexpected persistence error type: {type(error).__name__}")


async def _close_failed(
    session: AsyncSession, app_user_id: uuid.UUID, turn_id: uuid.UUID, error_code: str
) -> None:
    try:
        await fail_turn(session, app_user_id, turn_id, error_code=error_code)
    except (CompletionConflictError, ChatTurnNotFoundError):
        # Stale recovery or a concurrent writer already closed the row. Never overwrite.
        pass


async def run_persisted_turn(
    session_factory: async_sessionmaker[AsyncSession],
    app_user_id: uuid.UUID,
    *,
    session_id: uuid.UUID,
    client_message_id: uuid.UUID,
    question: str,
    active_dog_id: str | None,
    orchestrate: Orchestrate,
) -> AssistantResponse:
    """``reserve TX -> close AsyncSession -> orchestrate -> completion/failure TX``.

    No DB session or row lock is alive while ``orchestrate`` runs. An exact replay of a
    completed turn answers from the stored ``public_response`` and never calls it. The
    session's pet is authoritative: a conflicting ``active_dog_id`` is refused before any
    row is written, and the orchestrator always sees the session's pet.
    """
    _validate_question(question)

    async with session_factory() as reserve_session:
        # ``admin_or_app_user`` trusts the token alone. This path filters by identity, so it
        # repeats the check ``current_app_user`` makes for every other app-owned API: the
        # member must still be active, and the FOR UPDATE serializes with withdrawal.
        if await app_user_repo.get_active_for_update(reserve_session, app_user_id) is None:
            raise AppUserNotActiveError
        chat_session = await _require_owned_session(reserve_session, app_user_id, session_id)
        pet_id = str(chat_session.pet_id)
        if active_dog_id is not None and active_dog_id != pet_id:
            raise ActiveDogMismatchError(chat_session.pet_id)
        turn = await reserve_turn(
            reserve_session,
            app_user_id,
            session_id,
            client_message_id=client_message_id,
            question=question,
        )
        if turn.processing_status == "completed":
            return AssistantResponse.model_validate(turn.public_response)
        turn_id = turn.id

    try:
        response = await orchestrate(pet_id)
    except Exception:
        async with session_factory() as failure_session:
            await _close_failed(failure_session, app_user_id, turn_id, "ORCHESTRATION_FAILED")
        raise

    if response.status is AssistantStatus.FAILED:
        # The orchestrator could not answer (router/provider failure). That is a provider
        # failure with a polite message, not a delivered answer: keep the draft a draft.
        async with session_factory() as failure_session:
            await _close_failed(failure_session, app_user_id, turn_id, "ASSISTANT_FAILED")
        return response

    async with session_factory() as completion_session:
        try:
            await complete_turn(completion_session, app_user_id, turn_id, response=response)
        except _PERSISTENCE_FAILURES as exc:
            raise TurnPersistenceError(turn_id, _persistence_error_code(exc)) from exc
    return response


def _render_completed_turns(turns: list[ChatTurn]) -> str:
    return render_transcript(
        [(turn.user_content, turn.assistant_content or "") for turn in turns]
    )


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
    # The limit counts stored characters, the same way turn reservation and completion count
    # them, so a conversation that was allowed to grow can always be summarized. The JSON
    # envelope's overhead is covered by the byte-based Gemini input cap instead.
    if _transcript_char_count(turns) > MAX_TRANSCRIPT_CHARS:
        raise TranscriptLimitError
    transcript = _render_completed_turns(turns)

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


async def delete_summary(
    session: AsyncSession, app_user_id: uuid.UUID, summary_id: uuid.UUID
) -> None:
    """Delete one completed summary. The source conversation is untouched.

    A failed reservation is invisible to the user (only completed rows are listed), so it is
    reported as not found rather than deleted. A ``processing`` one is refused: deleting it
    would leave the in-flight completion UPDATE with no row, and the caller would then see a
    502 for a summary the user "already removed".
    """
    summary = await chat_repo.get_owned_summary(session, app_user_id, summary_id)
    if summary is None or summary.processing_status == "failed":
        raise ChatSummaryNotFoundError
    if summary.processing_status == "processing":
        raise SummaryProcessingError(summary.id)
    await chat_repo.delete_summary(session, summary)
    await session.commit()


__all__ = [
    "MAX_ASSISTANT_CHARS",
    "MAX_COMPLETED_TURNS",
    "MAX_FAILED_TURNS",
    "MAX_QUESTION_CHARS",
    "MAX_SESSIONS_PER_PET",
    "MAX_TRANSCRIPT_CHARS",
    "STALE_PROCESSING_AFTER",
    "ActiveDogMismatchError",
    "AppUserNotActiveError",
    "ChatSessionNotFoundError",
    "ChatSummaryNotFoundError",
    "ChatTurnNotFoundError",
    "CompletionConflictError",
    "ContentLimitError",
    "EmptyConversationError",
    "ExistingSummaryError",
    "Orchestrate",
    "PetNotOwnedError",
    "SummaryProcessingError",
    "SummaryRequestConflictError",
    "SummaryReservation",
    "TranscriptLimitError",
    "TurnFailedError",
    "TurnIdempotencyConflictError",
    "TurnLimitError",
    "TurnPersistenceError",
    "TurnProcessingError",
    "build_title",
    "categories_of",
    "complete_summary",
    "complete_turn",
    "create_session",
    "create_summary",
    "delete_session",
    "delete_summary",
    "fail_summary",
    "fail_turn",
    "get_session_with_turns",
    "list_sessions",
    "list_summaries",
    "public_response_of",
    "reserve_summary",
    "reserve_turn",
    "run_persisted_turn",
]
