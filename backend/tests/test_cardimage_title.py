from pathlib import Path

import numpy as np
from PIL import Image

from daengs_cardimage import catalog, title

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
    rows = np.where(bright.any(axis=1))[0] + 60
    cols = np.where(bright.any(axis=0))[0] + 250
    # 09-18 부터 사선은 잉크 맨 아래에서 잰다 — 그 높이의 경계 안이면 통과(옛 기준은 `758 + 20` 고정선이었다).
    assert cols.max() <= title._plate_right_edge(rows.max(), title.APRIL_PLATE.edge)


def test_short_name_is_not_condensed_and_fills_more_of_the_plate():
    """흔한 이름은 장평 100% 그대로 — 예전과 같은 모양이어야 한다."""
    tpl = _template()
    out = title.draw_title(tpl, "BLOSSOM 네오", FONT)
    assert out.size == (994, 1582)
    assert title._choose("BLOSSOM 네오", FONT, title.APRIL_PLATE, title.APRIL_PLATE.center_y) == (title.CAP_HEIGHT, 1.0)


def test_long_name_never_covers_the_badge_for_every_card():
    """12달 + 딸기·상추에서, 20+70% 안에 들어가는 긴 이름은 사선 경계를 넘지 않는다."""
    for selector in list(range(1, 13)) + list(catalog.KINDS):
        c = catalog.resolve(selector)
        tpl = Image.open(catalog.template_path(selector, CARDIMAGE)).convert("RGB")
        text = title.title_text(c.card_name, "BUTTERCUP")
        out = title.draw_title(tpl, text, FONT, plate=c.plate)
        a, ref = np.asarray(out).astype(int), np.asarray(tpl).astype(int)
        band = slice(c.plate.top_y, c.plate.top_y + 95)
        bright = (a[band, 250:900].min(axis=2) > 200) & (ref[band, 250:900].min(axis=2) <= 200)
        cols = np.where(bright.any(axis=0))[0] + 250
        limit = title._plate_right_edge(c.plate.center_y, c.plate.edge)
        assert cols.max() <= limit, (selector, cols.max(), limit)


def test_name_too_long_for_26_is_condensed_instead_of_shrunk_further():
    """26 에서 넘치면 높이를 더 깎지 않고 장평으로 줄인다 (사용자 결정 09-18)."""
    plate = title.APRIL_PLATE
    cap, ratio = title._choose(title.title_text("BLOSSOM", "PRINCESS BUTTERCUP"), FONT, plate, plate.center_y)
    assert cap == title.CONDENSE_CAP and title.CONDENSE_MIN <= ratio < 1.0


def test_name_too_long_even_for_20_and_70_percent_is_drawn_as_is_and_whole():
    """20·70% 로도 넘치면 그대로 그린다. 넓은 캔버스에 그려 줄이므로 끝 글자가 잘리지 않는다."""
    tpl = _template()
    plate = title.APRIL_PLATE
    text = title.title_text("BLOSSOM", "PRINCESSBUTTERCUPOFTHEHIGHLANDSXYZWQABCD")
    assert title._choose(text, FONT, plate, plate.center_y) == (title.MIN_CAP, title.CONDENSE_MIN)
    out = title.draw_title(tpl, text, FONT)
    a, ref = np.asarray(out).astype(int), np.asarray(tpl).astype(int)
    bright = (a[40:160, 250:994].min(axis=2) > 200) & (ref[40:160, 250:994].min(axis=2) <= 200)
    cols = np.where(bright.any(axis=0))[0] + 250
    f = title._font(FONT, title._size_for_cap(FONT, title.MIN_CAP))
    end = plate.left_x + (f.getbbox(text)[2] + title.STROKE_W) * title.CONDENSE_MIN
    assert end - 8 <= cols.max() <= end        # 마지막 글자까지 찍혔다
    assert plate.left_x + f.getbbox(text)[2] > out.width   # 안 줄인 글자는 카드 밖까지 간다(넓은 캔버스가 필요한 이유)


def _ink_center_y(out: Image.Image, ref: Image.Image, plate: title.Plate) -> float:
    """틀과 달라진 밝은 픽셀(우리가 찍은 글자)의 세로 중심."""
    a = np.asarray(out).astype(int)
    r = np.asarray(ref).astype(int)
    y0, y1 = plate.center_y - 60, plate.center_y + 60
    bright = (a[y0:y1, 250:800].min(axis=2) > 200) & (r[y0:y1, 250:800].min(axis=2) <= 200)
    rows = np.where(bright.any(axis=1))[0] + y0
    return (rows.min() + rows.max()) / 2


def test_september_plate_centers_text_higher_than_april():
    """9월 판은 4월보다 11px 위(y 40~135 vs 53~145, 09-14 실측)라 글자도 그만큼 위에 찍혀야 한다."""
    sep = Image.open(CARDIMAGE / "9_harvest_moon_template.webp").convert("RGB")
    sep_plate = catalog.SEPTEMBER_PLATE
    apr_c = _ink_center_y(title.draw_title(_template(), "CHUSEOK 네오", FONT), _template(), title.APRIL_PLATE)
    sep_c = _ink_center_y(title.draw_title(sep, "CHUSEOK 네오", FONT, plate=sep_plate), sep, sep_plate)
    assert abs(apr_c - 99) <= 4 and abs(sep_c - 88) <= 4
    assert 8 <= apr_c - sep_c <= 14


def test_template_plate_top_is_found_where_measured():
    assert title._plate_shift(_template(), title.APRIL_PLATE) == 0
    sep = Image.open(CARDIMAGE / "9_harvest_moon_template.webp").convert("RGB")
    assert title._plate_shift(sep, catalog.SEPTEMBER_PLATE) == 0


def test_text_follows_plate_drawn_higher_than_template():
    """모델 출력은 판이 틀보다 4~5px 위에 그려진다(09-14 실측). 카드를 통째로 6px 올린
    가짜 출력에 얹으면 글자도 6px 위에 찍혀야 한다."""
    tpl = _template()
    shifted = Image.new("RGB", tpl.size, (0, 0, 0))
    shifted.paste(tpl.crop((0, 6, tpl.width, tpl.height)), (0, 0))
    assert title._plate_shift(shifted, title.APRIL_PLATE) == -6
    base_c = _ink_center_y(title.draw_title(tpl, "BLOSSOM 네오", FONT), tpl, title.APRIL_PLATE)
    moved_c = _ink_center_y(title.draw_title(shifted, "BLOSSOM 네오", FONT), shifted, title.APRIL_PLATE)
    assert base_c - moved_c == 6


def test_unrecognisable_plate_falls_back_to_template_center():
    blank = Image.new("RGB", (994, 1582), (255, 255, 255))
    assert title._plate_shift(blank, title.APRIL_PLATE) == 0


def test_input_is_not_mutated():
    src = _template()
    before = np.asarray(src).copy()
    title.draw_title(src, "BLOSSOM 네오", FONT)
    assert (np.asarray(src) == before).all()
