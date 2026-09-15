"""`tools/cardgen_compare.py` 의 순수 부분 — 크기 파싱 · 아래 패널 문구 문장 · 프롬프트 감싸기 (#557 E1)."""

import argparse

import pytest

from tools.cardgen_compare import PANEL_TEXT, PromptSuffixEngine, panel_sentence, parse_size


def test_parse_size() -> None:
    assert parse_size("1280x2048") == (1280, 2048)
    assert parse_size("1024X1632") == (1024, 1632)
    for bad in ("1280", "x2048", "1280x", "axb", "1280x2048x1"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_size(bad)


def test_panel_text_covers_open_months_exactly() -> None:
    assert PANEL_TEXT[4] == ("PETAL PAUSE", "One petal. Perfect timing.", "SPRING", "920",
                             "Bloomed right on schedule.")
    assert PANEL_TEXT[9] == ("SONGPYEON SWEEP", "Full moon. Fuller snack tray.", "MOON LUCK", "925",
                             "A warm Chuseok surprise.")


def test_panel_sentence_quotes_every_text() -> None:
    sentence = panel_sentence(4)
    for text in PANEL_TEXT[4]:
        assert f'"{text}"' in sentence
    assert "letter for letter" in sentence


def test_panel_sentence_unknown_month_fails_loudly() -> None:
    with pytest.raises(KeyError):
        panel_sentence(1)


class _Inner:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.last_meta = {"seed": 1}

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        self.prompts.append(prompt)
        return b"card"


def test_prompt_suffix_engine_appends_and_exposes_meta() -> None:
    inner = _Inner()
    engine = PromptSuffixEngine(inner, "EXTRA")
    assert engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="BASE") == b"card"
    assert inner.prompts == ["BASE\n\nEXTRA"]
    assert engine.last_meta == {"seed": 1}
