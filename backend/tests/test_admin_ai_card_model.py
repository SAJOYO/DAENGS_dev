"""`models/admin_ai_card.py` 가 `db/init/41_admin_ai_cards.sql` 을 따라가는지 (모델은 SQL 을 따라가는 쪽)."""

from sqlalchemy import Integer, SmallInteger

from daengs_backend.models import ADMIN_AI_CARD_ENGINES, AdminAiCard


def test_admin_ai_card_table_matches_sql() -> None:
    t = AdminAiCard.__table__
    assert t.name == "admin_ai_cards"
    assert {c.name for c in t.columns} == {
        "id", "admin_user_id", "card_key", "dog_name", "title", "engine", "seed", "attempts",
        "likeness", "judge_note", "storage_key", "size_bytes", "width", "height", "elapsed_ms", "created_at",
    }
    assert not t.c.storage_key.nullable and not t.c.admin_user_id.nullable


def test_image_columns_are_not_null() -> None:
    """이 표에는 status 가 없다 — 다 만들어진 카드만 들어오므로 이미지 칸이 전부 채워진다.

    널 허용으로 풀리면 빈 카드 행이 목록에 선다."""
    t = AdminAiCard.__table__
    for name in ("card_key", "dog_name", "title", "engine", "attempts",
                 "storage_key", "size_bytes", "width", "height", "elapsed_ms"):
        assert not t.c[name].nullable, name
    # 검수를 끄고 뽑으면 비는 칸들.
    for name in ("seed", "likeness", "judge_note"):
        assert t.c[name].nullable, name


def test_seed_is_integer_not_smallinteger() -> None:
    """seed 는 32767 을 넘을 수 있다 — SmallInteger 로 좁아지면 조용히 잘린다 (`ai_cards` 와 같다)."""
    assert type(AdminAiCard.__table__.c.seed.type) is Integer
    assert type(AdminAiCard.__table__.c.attempts.type) is SmallInteger


def test_admin_user_id_cascades_to_admin_users() -> None:
    fks = list(AdminAiCard.__table__.c.admin_user_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "admin_users.id"
    assert fks[0].ondelete == "CASCADE"


def test_named_constraints_match_the_sql() -> None:
    names = {c.name for c in AdminAiCard.__table__.constraints if c.name}
    assert {
        "admin_ai_cards_engine", "admin_ai_cards_card_key",
        "admin_ai_cards_likeness", "admin_ai_cards_size",
    } <= names


def test_indexes_keep_desc_and_uniqueness() -> None:
    """`created_at DESC` 를 잃으면 목록이 조용히 오래된 것부터 나온다 (에러는 안 난다)."""
    indexes = {i.name: i for i in AdminAiCard.__table__.indexes}
    assert set(indexes) == {"idx_admin_ai_cards_created", "idx_admin_ai_cards_storage_key"}
    assert not indexes["idx_admin_ai_cards_created"].unique
    created = str(next(iter(indexes["idx_admin_ai_cards_created"].expressions)))
    assert "created_at DESC" in created
    assert indexes["idx_admin_ai_cards_storage_key"].unique


def test_engines() -> None:
    assert ADMIN_AI_CARD_ENGINES == ("gemini", "cardgen")
