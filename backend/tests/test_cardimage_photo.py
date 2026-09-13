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
