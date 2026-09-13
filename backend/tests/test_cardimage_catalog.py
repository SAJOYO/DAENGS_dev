from pathlib import Path

import pytest

from daengs_backend.services.cardimage import catalog


def test_april_card():
    c = catalog.get(4)
    assert c.stem == "4_blossom" and c.card_name == "BLOSSOM" and c.badge == "26APR"
    assert "picnic blanket" in c.scene


def test_paths_point_into_cardimage_dir():
    base = Path("/cardimage")
    assert catalog.template_path(4, base) == base / "4_blossom_template.webp"
    assert catalog.font_path(base) == base / "fonts" / "NotoSerifKR.ttf"


def test_unknown_month_raises():
    with pytest.raises(KeyError):
        catalog.get(13)


def test_closed_month_raises():
    with pytest.raises(catalog.MonthNotOpenError):
        catalog.require_open(9, frozenset({4}))
    assert catalog.require_open(4, frozenset({4})).month == 4


def test_month_with_empty_scene_is_not_open_even_if_listed():
    with pytest.raises(catalog.MonthNotOpenError):
        catalog.require_open(1, frozenset({1}))   # 틀은 있지만 무대 묘사가 비어 있다


def test_all_twelve_months_have_templates_in_repo():
    base = Path(__file__).resolve().parents[2] / "cardimage"
    for m in range(1, 13):
        assert catalog.template_path(m, base).exists(), m
