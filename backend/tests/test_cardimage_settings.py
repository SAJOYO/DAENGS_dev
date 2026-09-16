import pytest
from pydantic import ValidationError

from daengs_backend.config import Settings


def test_cardimage_defaults(monkeypatch):
    for k in ("DAENGS_CARDIMAGE_GEMINI_API_KEY", "DAENGS_CARDIMAGE_MONTHS", "DAENGS_CARDIMAGE_DIR"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.cardimage_model == "gemini-3.1-flash-image"
    assert s.cardimage_size == "2K"
    assert s.cardimage_judge_model == "gemini-3.1-flash-lite"
    assert s.cardimage_judge_min == 3
    assert s.cardimage_months == frozenset(range(1, 13))   # task-2(2026-09-16)부터 12달 전부
    assert s.cardimage_gemini_api_key.get_secret_value() == ""
    # 절대 경로다(#496 리뷰) — CWD 에 따라 갈리지 않는다. 이 체크아웃에는 진짜 틀이
    # 있으므로 `uv run dev` 를 backend/ 에서 돌려도 못 찾는 일이 없어야 한다.
    assert s.cardimage_dir.is_absolute()
    assert s.cardimage_dir.name == "cardimage"
    assert (s.cardimage_dir / "4_blossom_template.webp").exists()


def test_cardimage_months_parses_csv(monkeypatch):
    monkeypatch.setenv("DAENGS_CARDIMAGE_MONTHS", "4, 9,12")
    s = Settings(_env_file=None)
    assert s.cardimage_months == frozenset({4, 9, 12})


def test_default_months_open_all_twelve(monkeypatch):
    monkeypatch.delenv("DAENGS_CARDIMAGE_MONTHS", raising=False)
    s = Settings(_env_file=None)
    assert s.cardimage_months == frozenset(range(1, 13))


def test_cardgen_defaults_keep_gemini_path(monkeypatch):
    for k in ("DAENGS_CARDGEN_URL", "DAENGS_CARDGEN_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.cardgen_url == ""          # 비어 있으면 Nano Banana 2 (D-074) 그대로
    assert s.cardgen_timeout_s == 900.0  # 콜드 스타트(가중치 로드) + 생성


def test_cardimage_pick_count_defaults_to_two(monkeypatch):
    monkeypatch.delenv("DAENGS_CARDIMAGE_PICK_COUNT", raising=False)
    assert Settings(_env_file=None).cardimage_pick_count == 2


def test_cardimage_pick_count_above_two_is_rejected(monkeypatch):
    """#572 Task 4 fix round 1 controller ruling B — 사용자가 2장으로 정했다. 그 이상을 설정으로
    열면 (겹치는 seed 로) 같은 이미지 두 장에 돈을 두 번 내는 길도 같이 열린다."""
    monkeypatch.setenv("DAENGS_CARDIMAGE_PICK_COUNT", "3")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
