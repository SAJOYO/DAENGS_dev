from pathlib import Path

from PIL import Image

from daengs_cardimage import plate_probe
from daengs_cardimage.title import APRIL_PLATE, _plate_right_edge

CARDIMAGE = Path(__file__).resolve().parents[2] / "cardimage"


def test_probe_reproduces_measured_april_plate():
    """4월 틀은 이미 손으로 쟀다(center_y=99, top_y=52, edge y65→791). 도구가 그것을 재현해야 한다."""
    img = Image.open(CARDIMAGE / "4_blossom_template.webp").convert("RGB")
    got = plate_probe.measure(img)
    assert abs(got.top_y - APRIL_PLATE.top_y) <= 2
    assert abs(got.center_y - APRIL_PLATE.center_y) <= 2
    # 판 오른쪽 경계는 기울어져 있어 행(y)이 다르면 x 도 다르다 — probe 는 top+3(y=55) 행을
    # 보고하는데 손으로 잰 791 은 y=65 행 값이라, 그대로 비교하면 서로 다른 행을 맞대 놓고
    # "일치"라 우기는 셈이다(8px 어긋나도 우연히 그때는 통과했다). APRIL_PLATE 의 기울기로
    # probe 가 본 그 행의 x 를 보간해 같은 행끼리 비교한다.
    expected_x = _plate_right_edge(got.edge[0][0], APRIL_PLATE.edge)
    assert abs(got.edge[0][1] - expected_x) <= 6


def test_probe_reports_september_plate_higher_and_narrower_than_april():
    img = Image.open(CARDIMAGE / "9_harvest_moon_template.webp").convert("RGB")
    got = plate_probe.measure(img)
    assert got.center_y < APRIL_PLATE.center_y          # 9월은 11px 위
    assert got.edge[0][1] < APRIL_PLATE.edge[0][1]      # 그리고 약 40px 좁다
