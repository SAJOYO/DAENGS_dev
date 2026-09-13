from pathlib import Path

from daengs_backend.config import Settings


def test_cardimage_defaults(monkeypatch):
    for k in ("DAENGS_CARDIMAGE_GEMINI_API_KEY", "DAENGS_CARDIMAGE_MONTHS", "DAENGS_CARDIMAGE_DIR"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.cardimage_model == "gemini-3.1-flash-image"
    assert s.cardimage_size == "2K"
    assert s.cardimage_judge_model == "gemini-3.1-flash-lite"
    assert s.cardimage_judge_min == 3
    assert s.cardimage_months == frozenset({4})
    assert s.cardimage_gemini_api_key.get_secret_value() == ""
    assert s.cardimage_dir == Path("cardimage")


def test_cardimage_months_parses_csv(monkeypatch):
    monkeypatch.setenv("DAENGS_CARDIMAGE_MONTHS", "4, 9,12")
    s = Settings(_env_file=None)
    assert s.cardimage_months == frozenset({4, 9, 12})
