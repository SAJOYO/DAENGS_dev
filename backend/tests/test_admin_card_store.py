"""`services/admin_card_store.py` — 콘솔이 뽑은 카드의 저장·조회·삭제 (#592).

저장소는 임시 디렉터리 위의 **진짜** `LocalBridgeStorage` 입니다 (`test_ai_card_service.py` 와
같은 방식). 리포지토리 대역은 `fakes.install` 이 아니라 **이 파일 안**에 있습니다 — 콘솔 표는
콘솔만 쓰므로 공용 대역에 넣으면 상관없는 테스트가 그것까지 들고 다닙니다.
"""

import asyncio
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeSession
from PIL import Image

from daengs_backend.core.storage import LocalBridgeStorage, NotConfiguredStorage
from daengs_backend.models import AdminAiCard
from daengs_backend.repositories import admin_ai_card as admin_ai_card_repo
from daengs_backend.services import admin_card_store as service
from daengs_cardimage.judge import JudgeResult

ADMIN = uuid.uuid4()
OTHER_ADMIN = uuid.uuid4()

JUDGE = JudgeResult(likeness=4, text_ok=True, avatar_ok=True, note="얼굴이 사진과 닮았다")


class Rows:
    """`admin_ai_cards` 표 대역. 진짜 정렬 규칙(최근 것부터)만 흉내 냅니다."""

    def __init__(self) -> None:
        self.cards: list[AdminAiCard] = []


@pytest.fixture
def rows(monkeypatch: pytest.MonkeyPatch) -> Rows:
    store = Rows()

    def add(session, card):
        store.cards.append(card)
        return card

    async def get(session, card_id, *, for_update=False):
        return next((c for c in store.cards if c.id == card_id), None)

    async def list_recent(session, *, limit=10, before=None):
        # 진짜 쿼리와 같게 `(created_at, id) DESC` 이고 소유자로 거르지 않습니다.
        # `before` 도 같은 튜플 비교라, 키셋 규칙을 대역이 흉내 냅니다.
        ordered = sorted(store.cards, key=lambda c: (c.created_at, c.id), reverse=True)
        if before is not None:
            ordered = [c for c in ordered if (c.created_at, c.id) < before]
        return ordered[:limit]

    async def delete(session, card):
        store.cards.remove(card)

    monkeypatch.setattr(admin_ai_card_repo, "add", add)
    monkeypatch.setattr(admin_ai_card_repo, "get", get)
    monkeypatch.setattr(admin_ai_card_repo, "list_recent", list_recent)
    monkeypatch.setattr(admin_ai_card_repo, "delete", delete)
    return store


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> LocalBridgeStorage:
    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    monkeypatch.setattr(service, "get_storage", lambda: s)
    return s


def _png(size: tuple[int, int] = (994, 1582)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (7, 8, 9)).save(buf, "PNG")
    return buf.getvalue()


def _save(session: FakeSession, png: bytes, **kw) -> AdminAiCard | None:
    args = {
        "admin_user_id": ADMIN,
        "card_key": "strawberry",
        "dog_name": "네오",
        "title": "BERRY 네오",
        "engine": "gemini",
        "seed": None,
        "attempts": 1,
        "judge": JUDGE,
        "png": png,
        "elapsed_ms": 1234,
    }
    args.update(kw)
    return asyncio.run(service.save(session, **args))


def test_save_writes_the_png_and_the_row(rows: Rows, storage: LocalBridgeStorage) -> None:
    png = _png()
    session = FakeSession()

    card = _save(session, png)

    assert card is not None
    assert card.storage_key == f"admin-ai-cards/{ADMIN}/{card.id}.png"
    assert storage.local_path(card.storage_key).read_bytes() == png
    assert rows.cards == [card] and session.commits == 1
    assert (card.width, card.height) == (994, 1582) and card.size_bytes == len(png)
    assert card.card_key == "strawberry" and card.engine == "gemini" and card.attempts == 1
    assert card.likeness == 4 and card.judge_note == "얼굴이 사진과 닮았다"
    assert card.elapsed_ms == 1234 and card.seed is None
    # commit 뒤에 읽을 값이라 서버 기본값(NOW())에 기대지 않습니다.
    assert card.created_at is not None


def test_save_without_judge_leaves_the_score_empty(rows: Rows, storage: LocalBridgeStorage) -> None:
    card = _save(FakeSession(), _png(), judge=None, engine="cardgen", seed=7)

    assert card is not None and card.likeness is None and card.judge_note is None
    assert card.seed == 7 and card.engine == "cardgen"


def test_save_returns_none_when_storage_is_not_configured(
    rows: Rows, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GAIT_STORAGE` 가 꺼져 있으면 **예외가 아니라 `None`** 입니다 — 라우터가 `stored=false` 로
    응답하고 뽑은 카드는 그대로 줍니다(spec ④)."""
    monkeypatch.setattr(service, "get_storage", lambda: NotConfiguredStorage())
    session = FakeSession()

    card = _save(session, _png())

    assert card is None and rows.cards == [] and session.commits == 0


def test_recent_gives_every_admins_cards_newest_first(rows: Rows, storage: LocalBridgeStorage) -> None:
    mine = _save(FakeSession(), _png())
    theirs = _save(FakeSession(), _png(), admin_user_id=OTHER_ADMIN, card_key="lettuce")
    assert mine is not None and theirs is not None
    # 같은 밀리초에 저장될 수 있어 시각을 벌려 둡니다.
    mine.created_at = datetime(2026, 9, 18, 1, tzinfo=UTC)
    theirs.created_at = datetime(2026, 9, 18, 2, tzinfo=UTC)

    page = asyncio.run(service.recent(FakeSession()))

    assert [c.id for c in page.cards] == [theirs.id, mine.id]
    # 두 장뿐이라 다음 쪽이 없습니다 — 화면은 이 `None` 으로 「더 보기」를 지웁니다.
    assert page.next_cursor is None


def test_recent_pages_ten_at_a_time_and_the_cursor_continues(
    rows: Rows, storage: LocalBridgeStorage
) -> None:
    """기본 10장 + 커서로 이어받기 (사용자 09-18). 쪽이 겹치지도, 빠지지도 않습니다."""
    made = []
    for n in range(12):
        card = _save(FakeSession(), _png((10, 10)))
        assert card is not None
        card.created_at = datetime(2026, 9, 18, tzinfo=UTC) + timedelta(minutes=n)
        made.append(card)

    first = asyncio.run(service.recent(FakeSession()))
    assert len(first.cards) == 10 and first.next_cursor is not None
    assert [c.id for c in first.cards] == [c.id for c in reversed(made[2:])]

    second = asyncio.run(service.recent(FakeSession(), cursor=first.next_cursor))

    assert [c.id for c in second.cards] == [made[1].id, made[0].id]
    # 마지막 쪽이라 커서가 없습니다.
    assert second.next_cursor is None


def test_recent_rejects_a_cursor_we_did_not_bake(rows: Rows, storage: LocalBridgeStorage) -> None:
    """커서는 URL 에 실려 오므로 손으로 고친 값이 들어옵니다 — 500 이 아니라 여기서 잡습니다."""
    with pytest.raises(service.InvalidCursorError):
        asyncio.run(service.recent(FakeSession(), cursor="손으로-고친-값"))


def test_load_png_gives_the_row_and_the_bytes(rows: Rows, storage: LocalBridgeStorage) -> None:
    png = _png()
    card = _save(FakeSession(), png)
    assert card is not None

    got = asyncio.run(service.load_png(FakeSession(), card.id))

    assert got is not None
    row, data = got
    assert row is card and data == png
    assert asyncio.run(service.load_png(FakeSession(), uuid.uuid4())) is None


def test_remove_deletes_the_object_and_the_row_then_says_false(
    rows: Rows, storage: LocalBridgeStorage
) -> None:
    card = _save(FakeSession(), _png())
    assert card is not None
    path = storage.local_path(card.storage_key)
    session = FakeSession()

    assert asyncio.run(service.remove(session, card.id)) is True
    assert not path.exists() and rows.cards == [] and session.commits == 1
    assert asyncio.run(service.remove(FakeSession(), card.id)) is False


def test_remove_survives_an_object_that_is_already_gone(
    rows: Rows, storage: LocalBridgeStorage
) -> None:
    """객체만 먼저 사라진 행(손으로 지웠거나 볼륨을 갈았을 때)도 지울 수 있어야 합니다."""
    card = _save(FakeSession(), _png())
    assert card is not None
    storage.local_path(card.storage_key).unlink()

    assert asyncio.run(service.remove(FakeSession(), card.id)) is True
    assert rows.cards == []
