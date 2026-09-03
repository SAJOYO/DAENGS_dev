"""Static verification that SQL, ORM, indexes, FKs, and limits stay aligned."""

from pathlib import Path

from sqlalchemy import Uuid

from daengs_backend.models import ChatSession, ChatSummary, ChatTurn
from daengs_backend.services import chat as chat_service
from daengs_backend.services.chat_summary import MAX_GEMINI_INPUT_TOKENS

ROOT = Path(__file__).resolve().parents[2]
SQL_FILES = (
    ROOT / "db/init/07_chats.sql",
    ROOT / "db/migrations/2026-09-01_chats.sql",
)


def ondelete(model: type, column: str) -> str | None:
    return next(iter(model.__table__.c[column].foreign_keys)).ondelete


def test_final_table_names_replace_chat_messages() -> None:
    assert ChatTurn.__tablename__ == "chat_turns"
    for path in SQL_FILES:
        sql = path.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS chat_turns" in sql
        assert "chat_messages" not in sql


def test_fk_delete_rules_are_explicit() -> None:
    assert ondelete(ChatTurn, "session_id") == "CASCADE"
    assert ondelete(ChatSummary, "source_session_id") == "SET NULL"
    assert ChatSummary.__table__.c.source_session_id.nullable
    assert ondelete(ChatSession, "app_user_id") == "CASCADE"
    assert ondelete(ChatSession, "pet_id") == "CASCADE"


def test_client_ids_are_uuid_and_unique_in_the_right_scope() -> None:
    assert isinstance(ChatTurn.__table__.c.client_message_id.type, Uuid)
    assert isinstance(ChatSummary.__table__.c.client_request_id.type, Uuid)
    constraints = {constraint.name for constraint in ChatTurn.__table__.constraints}
    assert "chat_turns_session_client_key" in constraints


def test_session_uses_nullable_last_message_not_counters() -> None:
    columns = ChatSession.__table__.c
    assert columns.last_message_at.nullable
    assert "activated_at" not in columns
    assert "turn_count" not in columns
    indexes = {index.name: index for index in ChatSession.__table__.indexes}
    assert indexes["chat_sessions_one_draft_idx"].unique
    assert indexes["chat_sessions_one_draft_idx"].dialect_options["postgresql"]["where"] is not None


def test_summary_outputs_are_nullable_during_reservation() -> None:
    for name in (
        "title",
        "question_summary",
        "answer_summary",
        "key_points",
        "cautions",
        "source_citations",
        "model",
        "prompt_version",
        "completed_at",
    ):
        assert ChatSummary.__table__.c[name].nullable, name


def test_partial_source_unique_allows_failed_retry() -> None:
    index = next(
        index
        for index in ChatSummary.__table__.indexes
        if index.name == "chat_summaries_source_reservation_idx"
    )
    where = str(index.dialect_options["postgresql"]["where"])
    assert index.unique
    assert "processing" in where and "completed" in where
    assert "failed" not in where


def test_sql_contains_state_checks_limits_and_order_indexes() -> None:
    for path in SQL_FILES:
        sql = path.read_text(encoding="utf-8")
        assert "chat_turns_state_check" in sql
        assert "chat_summaries_state_check" in sql
        assert "char_length(user_content) BETWEEN 1 AND 2000" in sql
        assert "char_length(assistant_content) BETWEEN 1 AND 8000" in sql
        assert "source_turn_count BETWEEN 1 AND 30" in sql
        assert "chat_turns_session_order_idx" in sql
        assert "chat_summaries_scope_order_idx" in sql
        assert "chat_sessions_pet_idx" in sql
        assert "chat_summaries_source_session_idx" in sql


def test_unapplied_migration_creates_final_schema_without_alter() -> None:
    sql = SQL_FILES[1].read_text(encoding="utf-8")
    assert sql.count("CREATE TABLE IF NOT EXISTS") == 3
    assert "ALTER TABLE" not in sql
    assert "BEGIN;" in sql and "COMMIT;" in sql


def test_turns_are_written_through_assistant_query_and_read_back_typed() -> None:
    """The one execution path carries the chat fields; the read path carries the reply."""
    from daengs_backend.main import app

    spec = app.openapi()
    request_ref = spec["paths"]["/assistant/query"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    request_schema = spec["components"]["schemas"][request_ref.rsplit("/", 1)[1]]
    assert {"chat_session_id", "client_message_id"} <= set(request_schema["properties"])
    assert "chat_session_id" not in request_schema.get("required", [])

    turn_schema = spec["components"]["schemas"]["ChatTurnResponse"]
    assert "public_response" in turn_schema["properties"]
    assert "AssistantResponse" in str(turn_schema["properties"]["public_response"])


def test_documented_limits_match_service_constants() -> None:
    assert chat_service.MAX_QUESTION_CHARS == 2_000
    assert chat_service.MAX_ASSISTANT_CHARS == 8_000
    assert chat_service.MAX_COMPLETED_TURNS == 30
    assert chat_service.MAX_TRANSCRIPT_CHARS == 320_000
    assert MAX_GEMINI_INPUT_TOKENS == 900_000
    note = (ROOT / "docs/chat-transaction-flow.md").read_text(encoding="utf-8")
    for value in ("2,000", "8,000", "30", "320,000", "900,000"):
        assert value in note
