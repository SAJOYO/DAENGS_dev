"""`tools/cardgen_compare.py` 의 순수 부분 — 크기 파싱 · 아래 패널 문구 문장 · 프롬프트 감싸기 (#557 E1)."""

import argparse

import pytest

from tools.cardgen_compare import PANEL_TEXT, BatchReplayEngine, PromptSuffixEngine, panel_sentence, parse_size


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
        self.seeds: list[int | None] = []
        self.last_meta = {"seed": 1}

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str,
                 seed: int | None = None) -> bytes:
        self.prompts.append(prompt)
        self.seeds.append(seed)
        return b"card"


def test_prompt_suffix_engine_appends_and_exposes_meta() -> None:
    inner = _Inner()
    engine = PromptSuffixEngine(inner, "EXTRA")
    assert engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="BASE") == b"card"
    assert inner.prompts == ["BASE\n\nEXTRA"]
    assert engine.last_meta == {"seed": 1}


def test_prompt_suffix_engine_forwards_seed_to_inner() -> None:
    """#572 fix round 1 F3 — seed 를 받되 버리면 안쪽 엔진이 다른 seed 로 만든다."""
    inner = _Inner()
    engine = PromptSuffixEngine(inner, "EXTRA")
    engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="BASE", seed=42)
    assert inner.seeds == [42]


class _BatchInner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.last_meta = None

    def generate_batch(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str, count: int) -> list[bytes]:
        self.calls.append((prompt, count))
        self.last_meta = {"seeds": [5, 6], "seconds": 3.0, "model": "fake", "size": "1024x1632", "count": count}
        return [b"c0", b"c1"]


def test_batch_replay_calls_service_once_and_hands_out_each_card() -> None:
    inner = _BatchInner()
    engine = BatchReplayEngine(inner, 2)
    first = engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="P")
    assert engine.last_meta == {"seed": 5, "index": 0, "batch_seconds": 3.0, "model": "fake",
                                "size": "1024x1632", "count": 2}
    second = engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="P")
    assert (first, second) == (b"c0", b"c1")
    assert engine.last_meta["seed"] == 6 and engine.last_meta["index"] == 1
    assert inner.calls == [("P", 2)]
    with pytest.raises(RuntimeError):
        engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="P")


def test_batch_replay_engine_accepts_seed_without_forwarding_it() -> None:
    """#572 fix round 1 F3 — 배치는 요청 하나로 여러 장을 서비스가 알아서 만든다. `generate_card`
    가 seed 를 넘겨도(TypeError 를 막으려고 받는 것뿐) 장별 실제 seed 는 last_meta["seeds"] 그대로다."""
    inner = _BatchInner()
    engine = BatchReplayEngine(inner, 2)
    engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="P", seed=999)
    assert engine.last_meta["seed"] == 5  # 999 가 아니라 서비스가 준 값
