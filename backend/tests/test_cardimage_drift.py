"""틀 밀림·제목판 측정 (D-078 비교 판정). Qwen 계열의 "image drift" 를 숫자로 잡는 자리다."""

from PIL import Image, ImageChops

from daengs_backend.config import settings
from daengs_cardimage import catalog
from daengs_cardimage.drift import frame_drift
from daengs_cardimage.title import plate_shift


def _noise() -> Image.Image:
    return Image.effect_noise((994, 1582), 64).convert("RGB")


def test_identical_card_has_no_drift() -> None:
    tpl = _noise()
    drift = frame_drift(tpl, tpl.copy())
    assert (drift.dx, drift.dy) == (0, 0)
    assert drift.mad_at_zero == 0


def test_shifted_card_reports_the_shift() -> None:
    tpl = _noise()
    moved = ImageChops.offset(tpl, 4, -2)   # 내용이 오른쪽 4 · 위 2 로 밀림
    drift = frame_drift(tpl, moved)
    assert (drift.dx, drift.dy) == (4, -2)
    assert drift.mad_at_best < drift.mad_at_zero


def test_plate_shift_is_zero_on_template_and_follows_offset() -> None:
    card = catalog.get(4)
    tpl = Image.open(catalog.template_path(4, settings.cardimage_dir)).convert("RGB")
    assert plate_shift(tpl, card.plate) == 0
    assert plate_shift(ImageChops.offset(tpl, 0, 5), card.plate) == 5


def test_plate_shift_is_none_when_plate_cannot_be_found() -> None:
    card = catalog.get(4)
    blank = Image.new("RGB", (994, 1582), (240, 240, 240))
    assert plate_shift(blank, card.plate) is None
