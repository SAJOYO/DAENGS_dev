"""`services/ai_card_engine.py` — backend 가 카드 생성을 부르는 유일한 자리 (D-076)."""

import io

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from PIL import Image
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.services import ai_card_engine
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


def test_ready_check_passes_for_open_month_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    assert ai_card_engine.ready_check(4).month == 4


def test_ready_check_closed_month(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "cardimage_months", frozenset({4, 9}))
    with pytest.raises(MonthNotOpenError):
        ai_card_engine.ready_check(12)


def test_ready_check_without_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("   "))
    with pytest.raises(CardImageUnavailable):
        ai_card_engine.ready_check(4)


def test_ready_check_missing_assets_is_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "cardimage_dir", tmp_path)
    with pytest.raises(CardImageUnavailable):
        ai_card_engine.ready_check(4)


def test_generate_feeds_settings_into_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_months", frozenset({4, 9}))
    card = ai_card_engine.generate(
        photo=_jpeg(), content_type="image/jpeg", month=4, dog_name="네오",
        engine=FakeEngine(), judge=FakeJudge([5]),
    )
    assert card.title == "BLOSSOM 네오"
    assert card.attempts == 1
    assert Image.open(io.BytesIO(card.png)).size == (994, 1582)
