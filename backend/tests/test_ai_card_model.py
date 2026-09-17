"""`models/ai_card.py` 가 `db/init/38_ai_cards.sql` 을 따라가는지 (모델은 SQL 을 따라가는 쪽)."""

from sqlalchemy import Boolean, Integer

from daengs_backend.models import AI_CARD_STATUSES, AiCard, AiCardUsage


def test_columns_follow_sql() -> None:
    assert set(AiCard.__table__.c.keys()) == {
        "id", "app_user_id", "dog_id", "month", "dog_name", "title", "status", "error_code",
        "storage_key", "generation", "size_bytes", "width", "height", "likeness", "attempts", "seed",
        "pick_group", "created_at", "updated_at",
    }


def test_seed_column_is_integer_not_smallinteger() -> None:
    """seed 는 32767 을 넘을 수 있다 — SmallInteger 로 되돌아가면 조용히 잘린다(#572 Task 3a)."""
    assert type(AiCard.__table__.c.seed.type) is Integer


def test_seed_round_trips_on_the_model() -> None:
    card = AiCard(month=4, dog_name="네오", title="BLOSSOM 네오", status="ready", seed=1234567890)
    assert card.seed == 1234567890


def test_named_constraints_and_indexes() -> None:
    names = {c.name for c in AiCard.__table__.constraints if c.name}
    assert {
        "ai_cards_month", "ai_cards_dog_name", "ai_cards_status", "ai_cards_ready_set",
        "ai_cards_failed_code", "ai_cards_size", "ai_cards_likeness", "ai_cards_attempts",
    } <= names
    indexes = {i.name: i for i in AiCard.__table__.indexes}
    assert indexes["idx_ai_cards_one_generating"].unique
    one_generating_where = str(indexes["idx_ai_cards_one_generating"].dialect_options["postgresql"]["where"])
    assert "generating" in one_generating_where
    # #572 Task 4 fix round 1 Critical — 행 하나가 아니라 요청의 대표 행 하나만 본다.
    assert "id = pick_group" in one_generating_where
    assert indexes["idx_ai_cards_storage_key"].unique
    assert not indexes["ix_ai_cards_pick_group"].unique  # 형제 행이 같은 값을 공유하는 것이 정상이다


def test_statuses() -> None:
    assert AI_CARD_STATUSES == ("generating", "ready", "failed")


def test_usage_columns_follow_sql() -> None:
    """`db/init/39_ai_card_usage.sql` 을 따라간다 (#543, D-077)."""
    table = AiCardUsage.__table__
    assert table.name == "ai_card_usage"
    assert set(table.c.keys()) == {"card_id", "app_user_id", "used_at", "unfulfilled_attempt"}
    assert [c.name for c in table.primary_key.columns] == ["card_id"]


def test_usage_unfulfilled_attempt_is_not_null_and_false_by_default() -> None:
    """#572 Task 5 — 시도 표시 칸. 기본이 `true` 거나 비면 모든 사용 기록이 하루 한도에서 빠진다."""
    column = AiCardUsage.__table__.c.unfulfilled_attempt
    assert type(column.type) is Boolean
    assert not column.nullable
    assert str(column.server_default.arg) == "false"


def test_usage_card_id_has_no_foreign_key() -> None:
    """카드를 지워도 사용 기록은 남아야 한다 — FK 가 생기면 삭제가 기록을 끌고 가거나 막힌다."""
    assert not AiCardUsage.__table__.c.card_id.foreign_keys
    fks = list(AiCardUsage.__table__.c.app_user_id.foreign_keys)
    assert len(fks) == 1 and fks[0].target_fullname == "app_users.id" and fks[0].ondelete == "CASCADE"
    assert "idx_ai_card_usage_owner_used" in {i.name for i in AiCardUsage.__table__.indexes}
