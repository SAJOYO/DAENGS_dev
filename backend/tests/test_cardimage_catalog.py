import dataclasses
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


def test_only_october_hides_the_face():
    """본문에 얼굴이 안 보이는 틀은 10월(유령 천) 하나다. 7월 선글라스는 사용자 확인 결과 공통 앞부분으로 문제없다(09-18)."""
    assert [m for m in range(1, 13) if catalog.get(m).face_hidden] == [10]


def test_every_month_has_its_own_measured_plate():
    """제목판은 달마다 다르다. `!= APRIL_PLATE` 만 보면 두 달이 같은 틀린 plate 를 공유해도 못
    잡는다(최종 리뷰 minor) — 12달 plate 가 전부 서로 달라야 한다."""
    assert len({catalog.get(m).plate for m in range(1, 13)}) == 12


# --- #572 Task 3a: seeds / pick_seeds -------------------------------------------------


def test_every_month_has_at_least_two_distinct_verified_seeds():
    """#572 Task 3b — 12달 전부 눈으로 확인한 seed 를 갖는다(task-3b-visual-report.md).

    2개를 기준으로 삼는 이유: 한 요청이 카드 2장을 만드는데, 생성기는 같은 이미지에 두 번
    돈을 내지 않으려고 겹치는 seed 를 거부한다(#572 Task 4 fix round 1 Important 2). 검증된
    seed 가 2개 미만인 달은 이 규칙 때문에 조용히 카드 1장만 나가게 된다.
    """
    for m in range(1, 13):
        seeds = catalog.get(m).seeds
        assert len(seeds) >= 2, m
        assert len(set(seeds)) == len(seeds), m
        assert all(isinstance(s, int) and s > 0 for s in seeds), m


def test_pick_seeds_returns_distinct_values_from_the_month_list():
    rng = random.Random(0)
    picked = catalog.pick_seeds(4, 2, rng)
    assert len(picked) == len(set(picked)) == 2
    assert set(picked) <= set(catalog.get(4).seeds)


def test_pick_seeds_repeats_when_asked_for_more_than_the_list_has():
    """목록이 2개인데 4장을 뽑으면 되풀이한다 — 장수가 seed 수에 갇히지 않게."""
    rng = random.Random(0)
    assert len(catalog.pick_seeds(4, 4, rng)) == 4


def test_pick_seeds_falls_back_to_default_seeds_and_warns_for_unverified_month(monkeypatch, caplog):
    """검증 전 달은 raise 하지 않는다 — DEFAULT_SEEDS 로 대신하고 warning 을 남긴다(R5).

    #572 Task 3b 로 12달이 전부 검증돼 실제로 빈 달이 없어졌다 — 5월을 흉내 낸
    빈 `MonthCard` 를 직접 끼워 넣어 이 경로를 계속 검사한다.
    """
    monkeypatch.setitem(catalog._CARDS, 5, dataclasses.replace(catalog.get(5), seeds=()))
    rng = random.Random(0)
    with caplog.at_level(logging.WARNING, logger="daengs_cardimage.catalog"):
        picked = catalog.pick_seeds(5, 3, rng)
    assert len(picked) == 3
    assert set(picked) <= set(catalog.DEFAULT_SEEDS)
    assert any("month 5" in r.getMessage() for r in caplog.records)
