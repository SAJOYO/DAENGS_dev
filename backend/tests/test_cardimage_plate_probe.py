from pathlib import Path

from PIL import Image

from daengs_cardimage import plate_probe
from daengs_cardimage.title import APRIL_PLATE

CARDIMAGE = Path(__file__).resolve().parents[2] / "cardimage"


def test_probe_reproduces_measured_april_plate():
    """4월 틀은 이미 손으로 쟀다(center_y=99, top_y=52, edge y65→791). 도구가 그것을 재현해야 한다."""
    img = Image.open(CARDIMAGE / "4_blossom_template.webp").convert("RGB")
    got = plate_probe.measure(img)
    assert abs(got.top_y - APRIL_PLATE.top_y) <= 2
    assert abs(got.center_y - APRIL_PLATE.center_y) <= 2
    assert abs(got.edge[0][1] - 791) <= 6


def test_probe_reports_september_plate_higher_and_narrower_than_april():
    img = Image.open(CARDIMAGE / "9_harvest_moon_template.webp").convert("RGB")
    got = plate_probe.measure(img)
    assert got.center_y < APRIL_PLATE.center_y          # 9월은 11px 위
    assert got.edge[0][1] < APRIL_PLATE.edge[0][1]      # 그리고 약 40px 좁다
