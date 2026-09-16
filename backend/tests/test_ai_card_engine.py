"""`services/ai_card_engine.py` — backend 가 카드 생성을 부르는 유일한 자리 (D-076)."""

import io

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from PIL import Image
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.services import ai_card_engine, realtime_client
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError
from daengs_cardimage.engine import GeminiCardImageEngine, HttpCardImageEngine


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
    monkeypatch.setattr(settings, "cardgen_url", "")
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


def test_default_engine_is_gemini_when_cardgen_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardgen_url", "")
    assert isinstance(ai_card_engine.default_engine(), GeminiCardImageEngine)


def test_default_engine_is_http_when_cardgen_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardgen_url", " https://cardgen.example ")
    monkeypatch.setattr(settings, "cardgen_timeout_s", 30.0)
    engine = ai_card_engine.default_engine()
    assert isinstance(engine, HttpCardImageEngine)
    assert engine._base == "https://cardgen.example"
    assert engine._timeout_s == 30.0
    assert engine._auth is realtime_client.id_token


def test_ready_check_without_gemini_key_passes_when_cardgen_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr(""))
    monkeypatch.setattr(settings, "cardgen_url", "https://cardgen.example")
    assert ai_card_engine.ready_check(4).month == 4


def test_plan_seeds_delegates_to_cardimage() -> None:
    """#572 Task 4 fix round 1 — `start` 가 요청을 받는 순간 이것으로 seed 를 정한다."""
    import random

    from daengs_cardimage import catalog

    seeds = ai_card_engine.plan_seeds(4, 2, random.Random(0))
    assert len(seeds) == 2 and set(seeds) == set(catalog.get(4).seeds)


def test_plan_seeds_without_rng_still_works() -> None:
    assert len(ai_card_engine.plan_seeds(4, 1)) == 1


@pytest.mark.parametrize(("url", "gpu"), [("https://cardgen.example", True), (" https://x ", True), ("", False), ("  ", False)])
def test_plan_request_seeds_follows_gpu_path_active(monkeypatch: pytest.MonkeyPatch, url: str, gpu: bool) -> None:
    """#572 Task 8 — Nano Banana 2 경로는 seed 없는 한 장(재시도 경로), GPU 경로는 서로 다른 seed 로 pick_count 장."""
    import random

    monkeypatch.setattr(settings, "cardgen_url", url)
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    assert ai_card_engine.gpu_path_active() is gpu
    seeds = ai_card_engine.plan_request_seeds(4, random.Random(0))
    if gpu:
        assert len(seeds) == 2 and None not in seeds and len(set(seeds)) == 2
    else:
        assert seeds == [None]
