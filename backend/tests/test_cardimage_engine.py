import io
from types import SimpleNamespace

from PIL import Image

from daengs_cardimage import engine


def test_pad_to_2_3_adds_black_bars_symmetrically():
    card = Image.new("RGB", (994, 1582), (255, 0, 0))
    padded, pad = engine.pad_to_2_3(card)
    assert pad == 30 and padded.size == (1054, 1582)
    assert padded.getpixel((0, 0)) == (0, 0, 0) and padded.getpixel((pad + 1, 1)) == (255, 0, 0)


def test_fit_to_card_crops_bars_and_returns_card_size():
    gen = Image.new("RGB", (1696, 2528), (0, 255, 0))
    out = engine.fit_to_card(gen, pad=30, card_size=(994, 1582), padded_width=1054)
    assert out.size == (994, 1582)


def test_prompt_mentions_scene_badge_and_outfit():
    from daengs_cardimage.catalog import NO_OUTFIT

    p = engine.build_prompt(scene="the picnic blanket", badge="26APR", subtitle="APRIL SPECIAL", outfit=NO_OUTFIT)
    assert "the picnic blanket" in p and "26APR" in p and "APRIL SPECIAL" in p
    assert "no collar" in p and "circular portrait" in p


def test_prompt_uses_month_outfit_sentence_verbatim():
    p = engine.build_prompt(scene="s", badge="26SEP", subtitle="SEPTEMBER SPECIAL",
                            outfit="The dog must wear exactly the same hanbok as in image 1.")
    assert "exactly the same hanbok" in p and "wears nothing" not in p


def test_face_hidden_prompt_keeps_face_off_the_main_illustration():
    """10월처럼 옷이 얼굴까지 덮는 틀은 본문에 얼굴을 그리면 안 된다 — 공통 앞부분의 「귀·주둥이·눈 색」
    요구가 천 위에 실제 얼굴을 합성했다(09-16 FLUX.2-klein-4B, seed 2·3·4)."""
    p = engine.build_prompt(scene="s", badge="26OCT", subtitle="OCTOBER SPECIAL", outfit="ghost sheet", face_hidden=True)
    # 본문 강아지를 통째로 바꾸라는 공통 문장이 없어야 한다(배지 초상화에는 얼굴 특징을 여전히 요구한다).
    assert "in the main illustration is replaced by the dog from image 2" not in p
    assert "do not draw the dog's face" in p and "cut-out eyes and mouth" in p
    assert "the badge is the only place the dog's face appears" in p
    assert "paws" in p and "circular portrait" in p and "ghost sheet" in p and "26OCT" in p


def test_face_only_prompt_replaces_face_and_keeps_the_fruit_body():
    p = engine.build_prompt(scene="the leaf parachute", badge="NEO-S0824", subtitle="FRUIT DOG",
                            outfit="", face_only=True)
    assert "in the main illustration is replaced by the dog from image 2" not in p
    assert "only the dog's face is visible" in p and "do not draw the dog's body" in p
    assert "the leaf parachute" in p and "NEO-S0824" in p and "FRUIT DOG" in p
    assert "no collar" in p


def test_prompt_defaults_to_visible_face():
    p = engine.build_prompt(scene="s", badge="26APR", subtitle="APRIL SPECIAL", outfit="o")
    assert "in the main illustration is replaced by the dog from image 2" in p and "do not draw the dog's face" not in p


def test_month_prompts_are_unchanged_by_the_new_branches():
    """달 12장은 face_only/face_hidden 도입 전과 글자까지 같아야 한다(10월만 09-18 에 의도적으로 바뀜)."""
    from daengs_cardimage import catalog

    for m in range(1, 13):
        c = catalog.resolve(m)
        p = engine.build_prompt(scene=c.scene, badge=c.badge, subtitle=c.subtitle, outfit=c.outfit,
                                face_hidden=c.face_hidden, face_only=c.face_only)
        assert ("do not draw the dog's face" in p) == (m == 10)
        assert "only the dog's face is visible" not in p


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
