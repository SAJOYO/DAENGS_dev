from daengs_backend.config import Settings


def test_cardimage_defaults(monkeypatch):
    for k in ("DAENGS_CARDIMAGE_GEMINI_API_KEY", "DAENGS_CARDIMAGE_MONTHS", "DAENGS_CARDIMAGE_DIR"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.cardimage_model == "gemini-3.1-flash-image"
    assert s.cardimage_size == "2K"
    assert s.cardimage_judge_model == "gemini-3.1-flash-lite"
    assert s.cardimage_judge_min == 3
    assert s.cardimage_months == frozenset({4, 9})   # 4월(BLOSSOM)·9월(CHUSEOK) — 실험으로 검증된 둘
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
