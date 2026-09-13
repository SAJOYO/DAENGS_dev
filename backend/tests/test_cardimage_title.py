from pathlib import Path

import numpy as np
from PIL import Image

from daengs_backend.services.cardimage import title

CARDIMAGE = Path(__file__).resolve().parents[2] / "cardimage"
FONT = CARDIMAGE / "fonts" / "NotoSerifKR.ttf"


def _template() -> Image.Image:
    return Image.open(CARDIMAGE / "4_blossom_template.webp").convert("RGB")


def test_title_text_uppercases_latin_and_keeps_korean():
    assert title.title_text("BLOSSOM", "neo") == "BLOSSOM NEO"
    assert title.title_text("BLOSSOM", " 네오 ") == "BLOSSOM 네오"


def test_draws_bright_pixels_only_inside_plate_band():
    out = title.draw_title(_template(), "BLOSSOM 네오", FONT)
    a = np.asarray(out).astype(int)
    band = a[53:146, 250:800]                      # 검은 판 안
    assert (band.min(axis=2) > 200).sum() > 500     # 글자가 찍혔다
    outside = a[150:400, 250:800]                   # 판 아래(그림)는 원본과 같다
    ref = np.asarray(_template()).astype(int)[150:400, 250:800]
    assert np.abs(outside - ref).max() == 0


def test_long_name_stays_left_of_badge_boundary():
    tpl = _template()
    out = title.draw_title(tpl, "BLOSSOM NEEEEEEEEEEEEO", FONT)
    a = np.asarray(out).astype(int)
    ref = np.asarray(tpl).astype(int)
    # 틀 자체(배지 장식)가 이미 이 구간에 밝은 픽셀을 가지고 있어(실측), 틀과 다른
    # 픽셀만 봐야 "우리가 새로 찍은 글자"만 잰다.
    bright = (a[60:140, 250:800].min(axis=2) > 200) & (ref[60:140, 250:800].min(axis=2) <= 200)
    cols = np.where(bright.any(axis=0))[0] + 250
    assert cols.max() <= 758 + 20   # 기울어진 경계(아래쪽 758)에 여백 14 를 둔 선 안


def test_input_is_not_mutated():
    src = _template()
    before = np.asarray(src).copy()
    title.draw_title(src, "BLOSSOM 네오", FONT)
    assert (np.asarray(src) == before).all()
