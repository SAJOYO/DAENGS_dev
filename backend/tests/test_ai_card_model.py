"""`models/ai_card.py` 가 `db/init/38_ai_cards.sql` 을 따라가는지 (모델은 SQL 을 따라가는 쪽)."""

from daengs_backend.models import AI_CARD_STATUSES, AiCard


def test_columns_follow_sql() -> None:
    assert set(AiCard.__table__.c.keys()) == {
        "id", "app_user_id", "dog_id", "month", "dog_name", "title", "status", "error_code",
        "storage_key", "generation", "size_bytes", "width", "height", "likeness", "attempts",
        "created_at", "updated_at",
    }


def test_named_constraints_and_indexes() -> None:
    names = {c.name for c in AiCard.__table__.constraints if c.name}
    assert {
        "ai_cards_month", "ai_cards_dog_name", "ai_cards_status", "ai_cards_ready_set",
        "ai_cards_failed_code", "ai_cards_size", "ai_cards_likeness", "ai_cards_attempts",
    } <= names
    indexes = {i.name: i for i in AiCard.__table__.indexes}
    assert indexes["idx_ai_cards_one_generating"].unique
    assert "generating" in str(indexes["idx_ai_cards_one_generating"].dialect_options["postgresql"]["where"])
    assert indexes["idx_ai_cards_storage_key"].unique


def test_statuses() -> None:
    assert AI_CARD_STATUSES == ("generating", "ready", "failed")
