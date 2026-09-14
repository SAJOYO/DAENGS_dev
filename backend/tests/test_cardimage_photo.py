import io

import pytest
from PIL import Image

from daengs_backend.services.cardimage import photo


def _jpeg(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 120, 80)).save(buf, "JPEG")
    return buf.getvalue()


def test_downscales_long_side_to_1600():
    out = photo.prepare_photo(_jpeg(3000, 4000), "image/jpeg")
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG" and max(im.size) == 1600 and im.size == (1200, 1600)


def test_small_photo_is_not_upscaled():
    out = photo.prepare_photo(_jpeg(800, 600), "image/jpeg")
    assert Image.open(io.BytesIO(out)).size == (800, 600)


def test_rejects_bad_mime():
    with pytest.raises(photo.PhotoError) as e:
        photo.prepare_photo(_jpeg(10, 10), "image/gif")
    assert e.value.code == "bad_mime"


def test_rejects_too_large():
    with pytest.raises(photo.PhotoError) as e:
        photo.prepare_photo(b"x" * (photo.MAX_PHOTO_BYTES + 1), "image/jpeg")
    assert e.value.code == "too_large"


def test_rejects_undecodable():
    with pytest.raises(photo.PhotoError) as e:
        photo.prepare_photo(b"not an image", "image/png")
    assert e.value.code == "undecodable"


def test_rejects_decompression_bomb():
    # 단색이라 압축은 잘 되지만(파일은 작다) 선언된 화소 수는 MAX_PIXELS 를 넘는다.
    buf = io.BytesIO()
    Image.new("RGB", (9000, 9000), (200, 120, 80)).save(buf, "PNG")
    with pytest.raises(photo.PhotoError) as e:
        photo.prepare_photo(buf.getvalue(), "image/png")
    assert e.value.code == "too_large"


def test_flattens_transparency_onto_white():
    buf = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buf, "PNG")  # 완전 투명, RGB 는 검정
    out = photo.prepare_photo(buf.getvalue(), "image/png")
    r, g, b = Image.open(io.BytesIO(out)).convert("RGB").getpixel((5, 5))
    assert r >= 250 and g >= 250 and b >= 250
