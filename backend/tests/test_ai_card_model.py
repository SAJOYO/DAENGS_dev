"""`models/ai_card.py` 가 `db/init/38_ai_cards.sql` 을 따라가는지 (모델은 SQL 을 따라가는 쪽)."""

from daengs_backend.models import AI_CARD_STATUSES, AiCard, AiCardUsage


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


def test_usage_columns_follow_sql() -> None:
    """`db/init/39_ai_card_usage.sql` 을 따라간다 (#543, D-077)."""
    table = AiCardUsage.__table__
    assert table.name == "ai_card_usage"
    assert set(table.c.keys()) == {"card_id", "app_user_id", "used_at"}
    assert [c.name for c in table.primary_key.columns] == ["card_id"]


def test_usage_card_id_has_no_foreign_key() -> None:
    """카드를 지워도 사용 기록은 남아야 한다 — FK 가 생기면 삭제가 기록을 끌고 가거나 막힌다."""
    assert not AiCardUsage.__table__.c.card_id.foreign_keys
    fks = list(AiCardUsage.__table__.c.app_user_id.foreign_keys)
    assert len(fks) == 1 and fks[0].target_fullname == "app_users.id" and fks[0].ondelete == "CASCADE"
    assert "idx_ai_card_usage_owner_used" in {i.name for i in AiCardUsage.__table__.indexes}
