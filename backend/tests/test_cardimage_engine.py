import io
from types import SimpleNamespace

from PIL import Image

from daengs_backend.services.cardimage import engine


def test_pad_to_2_3_adds_black_bars_symmetrically():
    card = Image.new("RGB", (994, 1582), (255, 0, 0))
    padded, pad = engine.pad_to_2_3(card)
    assert pad == 30 and padded.size == (1054, 1582)
    assert padded.getpixel((0, 0)) == (0, 0, 0) and padded.getpixel((pad + 1, 1)) == (255, 0, 0)


def test_fit_to_card_crops_bars_and_returns_card_size():
    gen = Image.new("RGB", (1696, 2528), (0, 255, 0))
    out = engine.fit_to_card(gen, pad=30, card_size=(994, 1582), padded_width=1054)
    assert out.size == (994, 1582)


def test_prompt_mentions_scene_badge_and_no_accessories():
    p = engine.build_prompt(scene="the picnic blanket", badge="26APR", subtitle="APRIL SPECIAL")
    assert "the picnic blanket" in p and "26APR" in p and "APRIL SPECIAL" in p
    assert "no collar" in p and "circular portrait" in p


def test_gemini_engine_without_key_raises_no_key():
    e = engine.GeminiCardImageEngine(api_key="", model="m", size="2K", timeout_ms=1000)
    try:
        e.generate(template_png=b"", photo_jpeg=b"", prompt="x")
    except engine.EngineError as err:
        assert err.code == "no_key"
    else:
        raise AssertionError("expected EngineError")


def test_extract_image_bytes_no_candidates_raises_no_image():
    resp = SimpleNamespace(candidates=[])
    try:
        engine._extract_image_bytes(resp)
    except engine.EngineError as err:
        assert err.code == "no_image"
    else:
        raise AssertionError("expected EngineError")


def test_extract_image_bytes_none_candidates_raises_no_image():
    resp = SimpleNamespace(candidates=None)
    try:
        engine._extract_image_bytes(resp)
    except engine.EngineError as err:
        assert err.code == "no_image"
    else:
        raise AssertionError("expected EngineError")


def test_extract_image_bytes_content_none_raises_no_image():
    resp = SimpleNamespace(candidates=[SimpleNamespace(content=None)])
    try:
        engine._extract_image_bytes(resp)
    except engine.EngineError as err:
        assert err.code == "no_image"
    else:
        raise AssertionError("expected EngineError")


def test_extract_image_bytes_text_only_raises_no_image_with_text():
    part = SimpleNamespace(inline_data=None, text="Sorry, I can't do that")
    resp = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])
    try:
        engine._extract_image_bytes(resp)
    except engine.EngineError as err:
        assert err.code == "no_image"
        assert "Sorry, I can't do that" in err.detail
    else:
        raise AssertionError("expected EngineError")


def test_extract_image_bytes_returns_inline_data():
    part = SimpleNamespace(inline_data=SimpleNamespace(data=b"PNGDATA"), text=None)
    resp = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])
    assert engine._extract_image_bytes(resp) == b"PNGDATA"


def test_decode_and_fit_garbage_bytes_raises_no_image():
    try:
        engine._decode_and_fit(b"not a png", pad=30, padded_width=1054)
    except engine.EngineError as err:
        assert err.code == "no_image"
    else:
        raise AssertionError("expected EngineError")


def test_decode_and_fit_valid_png_returns_card_size():
    buf = io.BytesIO()
    Image.new("RGB", (1696, 2528), (0, 255, 0)).save(buf, "PNG")
    out = engine._decode_and_fit(buf.getvalue(), pad=30, padded_width=1054)
    img = Image.open(io.BytesIO(out))
    assert img.size == (994, 1582)
    assert img.format == "PNG"
