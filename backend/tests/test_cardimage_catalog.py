import logging
import random
from pathlib import Path

import pytest

from daengs_cardimage import catalog


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


def test_september_card_is_openable_with_hanbok_outfit_and_own_edge():
    c = catalog.require_open(9, frozenset({4, 9}))
    assert c.card_name == "CHUSEOK" and c.badge == "26SEP" and c.subtitle == "SEPTEMBER SPECIAL"
    assert "hanbok" in c.outfit and "songpyeon" in c.scene
    # 9월 판은 4월보다 위에 있고(중심 88 < 99) 좁다(오른쪽 경계 750 < 791). 왼쪽 시작은 같다.
    assert c.plate.center_y < catalog.APRIL_PLATE.center_y
    assert c.plate.edge[0][1] < catalog.APRIL_PLATE.edge[0][1]
    assert c.plate.left_x == catalog.APRIL_PLATE.left_x


def test_april_uses_default_outfit_and_plate():
    c = catalog.get(4)
    assert c.outfit == catalog.NO_OUTFIT and c.plate == catalog.APRIL_PLATE and c.subtitle == "APRIL SPECIAL"


def test_month_with_empty_scene_is_not_open_even_if_listed(monkeypatch):
    """1월은 이제 열려 있으므로, 존재하지 않는 달 대신 빈 MonthCard 를 직접 만들어 검사한다."""
    blank = catalog.MonthCard(1, "1_new_year", "NEW YEAR", "26JAN", "", "JANUARY SPECIAL")
    monkeypatch.setitem(catalog._CARDS, 1, blank)
    with pytest.raises(catalog.MonthNotOpenError):
        catalog.require_open(1, frozenset({1}))


def test_all_twelve_months_have_templates_in_repo():
    base = Path(__file__).resolve().parents[2] / "cardimage"
    for m in range(1, 13):
        assert catalog.template_path(m, base).exists(), m


def test_every_month_is_openable():
    """12달 전부 무대 문장과 부제를 갖는다 — 하나라도 비면 require_open 이 404 로 막는다."""
    for m in range(1, 13):
        c = catalog.require_open(m, frozenset(range(1, 13)))
        assert c.scene.strip(), m
        assert c.subtitle.strip(), m


def test_every_month_has_its_own_measured_plate():
    """제목판은 달마다 다르다. 4월 말고 다른 달이 APRIL_PLATE 를 그대로 쓰면 제목이 어긋난다."""
    others = [catalog.get(m).plate for m in range(1, 13) if m != 4]
    assert all(p != catalog.APRIL_PLATE for p in others)


# --- #572 Task 3a: seeds / pick_seeds -------------------------------------------------


def test_only_months_four_and_nine_have_verified_seeds():
    """#557 실험에서 확인된 두 달만 채워져 있다 — 나머지 열은 3b 가 채우기 전까지 비어 있다(R5)."""
    assert catalog.get(4).seeds == (3, 4)
    assert catalog.get(9).seeds == (1, 4)
    for m in range(1, 13):
        if m in (4, 9):
            continue
        assert catalog.get(m).seeds == (), m


def test_pick_seeds_returns_distinct_values_from_the_month_list():
    rng = random.Random(0)
    picked = catalog.pick_seeds(4, 2, rng)
    assert len(picked) == len(set(picked)) == 2
    assert set(picked) <= set(catalog.get(4).seeds)


def test_pick_seeds_repeats_when_asked_for_more_than_the_list_has():
    """목록이 2개인데 4장을 뽑으면 되풀이한다 — 장수가 seed 수에 갇히지 않게."""
    rng = random.Random(0)
    assert len(catalog.pick_seeds(4, 4, rng)) == 4


def test_pick_seeds_falls_back_to_default_seeds_and_warns_for_unverified_month(caplog):
    """검증 전 달(예: 5월)은 raise 하지 않는다 — DEFAULT_SEEDS 로 대신하고 warning 을 남긴다(R5)."""
    rng = random.Random(0)
    with caplog.at_level(logging.WARNING, logger="daengs_cardimage.catalog"):
        picked = catalog.pick_seeds(5, 3, rng)
    assert len(picked) == 3
    assert set(picked) <= set(catalog.DEFAULT_SEEDS)
    assert any("month 5" in r.getMessage() for r in caplog.records)
