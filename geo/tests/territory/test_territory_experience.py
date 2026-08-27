"""경험 질의 계약. `app/features/territory/experience.py`.

장면(`docs/explorations/walk/experience-scenario.md`)의 3·4·5 번이 숫자로 나오는지 본다.

여기서 고정하는 것 넷.

1. **심은 조건 편향이 조건 칩으로 회수된다** — 장면 3 번이 성립하는 최소 조건
2. **분모가 결과까지 살아 온다** — 화면이 "5/7" 과 "17/24" 를 가를 수 있어야 한다
3. **`now` 가 인자다** — 같은 입력이 늘 같은 답을 낸다
4. **카드는 같은 표의 정렬 셋이고 서로 안 겹친다** — 한 영역이 두 카드에 나오면 자기모순
"""

import math
from datetime import UTC, datetime, timedelta

from app.features.territory.experience import (
    CHIPS,
    RECENT_DAYS,
    UNEXPLORED_CUT,
    NamedRegion,
    build,
    chip_selector,
    previous_window,
    recent_window,
    region_stats,
)
from app.features.territory.layers import Projection
from app.features.territory.paint import NARROW_STEP, paint_sheet
from app.features.territory.region import Region
from app.features.walk.facts import compute_facts
from app.features.walk.models import WalkFix

EARTH_R = 6_371_000.0
RADIUS_U = 8.0
LAT, LNG = 37.4979, 127.0276
NOW = datetime(2026, 8, 26, 18, 30, tzinfo=UTC)          # 여름 · 저녁
PROJECTION = Projection(radius_u=RADIUS_U, brush=NARROW_STEP.name,
                        profile_fp=NARROW_STEP.fingerprint)


def _east(x_m: float) -> float:
    return LNG + math.degrees(x_m / (EARTH_R * math.cos(math.radians(LAT))))


def _north(y_m: float) -> float:
    return LAT + math.degrees(y_m / EARTH_R)


def _box(region_id: str, x0: float, x1: float) -> Region:
    """동서 x0~x1, 남북 ±120m 의 사각 영역."""
    return Region(id=region_id, version=1,
                  ring=((_north(-120.0), _east(x0)), (_north(-120.0), _east(x1)),
                        (_north(120.0), _east(x1)), (_north(120.0), _east(x0))))


# 서로 안 겹치는 후보 넷. 사람이 그린 것이라는 뜻으로 이름을 준다.
NORTH = NamedRegion(_box("north", 200.0, 900.0), "양재천 북쪽")
PARK = NamedRegion(_box("park", 1_400.0, 2_100.0), "도곡공원")
SOUTH = NamedRegion(_box("south", 2_600.0, 3_300.0), "단지 남쪽")
FAR = NamedRegion(_box("far", 3_800.0, 4_500.0), "가본 적 없는 블록")
REGIONS = [NORTH, PARK, SOUTH, FAR]


def _walk(walk_id: str, at: datetime, x0: float, x1: float):
    """x0 → x1 을 1m/s 로 걷는 산책 하나."""
    step = 1.0 if x1 >= x0 else -1.0
    xs = [x0 + step * i for i in range(int(abs(x1 - x0)) + 1)]
    fixes = [
        WalkFix(client_seq=i, chain_index=0, at=at + timedelta(seconds=i),
                lat=LAT, lng=_east(x), accuracy_m=3.0, is_mock=False)
        for i, x in enumerate(xs)
    ]
    segs = compute_facts("w", "d", fixes[0].at, fixes[-1].at + timedelta(seconds=1),
                         fixes).segments
    return paint_sheet(walk_id, at, segs, RADIUS_U, NARROW_STEP)


def _planted():
    """정답을 심은 자료.

        저녁 산책 30 회  →  20 회는 북쪽(200~900), 10 회는 남쪽
        아침 산책 20 회  →  전부 공원(1400~2100)
        남쪽             →  그 10 회가 전부 40~49 일 전이다 (앞 창에만 있다)
        먼 블록          →  한 번도 안 갔다

    조건 칩이 이 편향을 되돌려주는지가 장면 3 번의 최소 조건이다. 전체 지도만 보면 북쪽과
    공원이 둘 다 진해서 **저녁 쏠림이 안 보인다** — 그게 셀로판을 만든 이유이기도 하다.
    """
    sheets = []
    for i in range(20):
        day = NOW - timedelta(days=i + 1)
        sheets.append(_walk(f"eve{i}", day.replace(hour=18), 250.0, 850.0))
        sheets.append(_walk(f"morn{i}", day.replace(hour=8), 1_450.0, 2_050.0))
    # 남쪽: 40~59 일 전에만 (앞 창에는 있고 최근 창에는 없다)
    for i in range(10):
        day = NOW - timedelta(days=40 + i)
        sheets.append(_walk(f"south{i}", day.replace(hour=18), 2_650.0, 3_250.0))
    return sheets


# ---- 1. 심은 편향이 회수된다 -----------------------------------------------------------


def test_the_condition_chip_recovers_the_planted_bias():
    """**이 파일의 핵심.** 전체로 보면 안 보이는 쏠림이 조건 칩에서 갈린다."""
    sheets = _planted()
    north = region_stats(sheets, NORTH, NOW, PROJECTION)
    park = region_stats(sheets, PARK, NOW, PROJECTION)

    # 전체 지도에서는 둘이 비슷하다 — 여기서만 보면 저녁 쏠림을 못 본다
    assert abs(north.by_chip["all"].rate - park.by_chip["all"].rate) < 0.05

    # 저녁 조건에서 갈린다. 저녁 산책 30 회 중 북쪽이 20 회, 공원은 0 회
    assert (north.by_chip["evening"].visited, north.by_chip["evening"].selected) == (20, 30)
    assert park.by_chip["evening"].visited == 0
    # 아침은 정반대 — 아침 20 회가 전부 공원이다
    assert (park.by_chip["morning"].visited, park.by_chip["morning"].selected) == (20, 20)
    assert north.by_chip["morning"].visited == 0


def test_every_chip_resolves_and_keeps_its_own_denominator():
    """칩마다 분모가 다르다 — 그래야 조건별 비율을 견줄 수 있다."""
    stats = region_stats(_planted(), NORTH, NOW, PROJECTION)
    assert set(stats.by_chip) == {chip for chip, _ in CHIPS}
    assert stats.by_chip["evening"].selected < stats.by_chip["all"].selected
    for chip, rate in stats.by_chip.items():
        assert rate.visited <= rate.selected <= rate.total, chip


# ---- 2. 분모가 결과까지 살아 온다 ------------------------------------------------------


def test_the_result_carries_numerator_and_denominator_not_just_a_ratio():
    """화면이 "5/7" 과 "17/24" 를 가를 수 있어야 한다."""
    stats = region_stats(_planted(), NORTH, NOW, PROJECTION)
    evening = stats.by_chip["evening"]
    assert (evening.visited, evening.selected) == (20, 30)
    assert evening.rate == 20 / 30
    assert stats.trend.recent.selected and stats.trend.previous.selected is not None


def test_a_trend_from_a_thin_window_is_flagged_not_hidden():
    """표본이 얇으면 추세라고 부르지 않는다 — 값은 그대로 주되 꼬리표를 단다.

    `1/2 → 2/2` 도 +0.5 고 `40/80 → 80/80` 도 +0.5 다. 화면이 이 둘을 같은 문장으로 쓰면
    U2 가 피해 온 "비율의 거짓 확신" 을 화면에서 저지르는 셈이다.
    """
    thin = [
        _walk("a", NOW - timedelta(days=5), 250.0, 850.0),
        _walk("b", NOW - timedelta(days=40), 250.0, 850.0),
    ]
    assert region_stats(thin, NORTH, NOW, PROJECTION).trend.trustworthy is False
    assert region_stats(_planted(), NORTH, NOW, PROJECTION).trend.trustworthy is True


# ---- 3. 결정론 -------------------------------------------------------------------------


def test_now_is_an_argument_so_the_same_input_gives_the_same_answer():
    """시계를 읽는 곳이 하나도 없어야 JSON 이 deterministic 하다."""
    sheets = _planted()
    first = build(sheets, REGIONS, NOW, PROJECTION)
    second = build(sheets, REGIONS, NOW, PROJECTION)
    assert first == second


def test_moving_now_moves_the_recent_window():
    """`now` 가 실제로 듣는지 — 안 들으면 위 테스트가 우연히 통과한다."""
    sheets = _planted()
    here = region_stats(sheets, NORTH, NOW, PROJECTION)
    later = region_stats(sheets, NORTH, NOW + timedelta(days=90), PROJECTION)
    assert here.by_chip["recent"].selected > 0
    assert later.by_chip["recent"].selected == 0


def test_the_recent_window_is_a_window_not_an_open_ended_since():
    """**실제로 났던 버그의 회귀 테스트.** 리팩터링 중에 한 번 잃어버렸다 다시 넣는다.

    "최근 30 일" 을 `since` 만으로 만들면 `now` 뒤의 산책까지 전부 들어온다. `now` 가 늘
    자료 끝보다 뒤일 때는 티가 안 나다가, 자료 **한가운데**를 "지금" 으로 잡는 순간 최근
    30 일이 261 회가 됐다.

    위 `test_moving_now_moves_the_recent_window` 로는 이 회귀를 못 잡는다 — `until` 을
    또 지워도 `NOW + 90일` 은 `since` 가 자료보다 미래라 0 개로 나와서 그냥 통과한다.
    **자료 한가운데에 서야** 잡힌다.
    """
    middle = NOW - timedelta(days=10)
    stats = region_stats(_planted(), NORTH, middle, PROJECTION)
    assert stats.by_chip["recent"].selected < stats.by_chip["all"].selected

    window = chip_selector("recent", middle.date())
    future = [s for s in _planted() if s.at.date() > middle.date()]
    assert future, "미래 쪽 산책이 없으면 이 테스트가 뜻이 없다"
    assert all(not window.matches(s.at) for s in future), "`지금` 뒤의 산책이 들어왔다"


def test_the_recent_window_is_exactly_thirty_days_and_abuts_the_previous_one():
    """"최근 30 일" 이라고 **말하니까** 30 일이어야 한다.

    `today - 30` 은 양끝을 다 세면 31 개 날짜다. 앞 창은 30 개였으므로 추세가 조용히
    31 대 30 을 견주고 있었다. 화면 문구가 계약이라 맞춰 둔다.
    """
    today = NOW.date()
    r_since, r_until = recent_window(today)
    p_since, p_until = previous_window(today)
    assert (r_until - r_since).days + 1 == RECENT_DAYS == 30
    assert (p_until - p_since).days + 1 == RECENT_DAYS
    assert p_until == r_since - timedelta(days=1), "두 창이 붙어 있지도 겹치지도 않아야 한다"


def test_chip_selector_rejects_an_unknown_chip():
    """화면과 질의가 같은 사전을 쓴다 — 오타가 빈 지도로 조용히 안 흐르게."""
    try:
        chip_selector("gloaming", NOW.date())
    except ValueError as err:
        assert "gloaming" in str(err)
    else:
        raise AssertionError("모르는 칩인데 통과했다")


# ---- 4. 고르기는 여기 없다 -------------------------------------------------------------
#
# 처음엔 이 파일이 카드(익숙한 곳/덜 간 곳/미개척)까지 테스트했다. 고르기가 그 자체로
# 장치라 `evidence.py` 로 갈랐고, 그쪽 계약은 `test_territory_evidence.py` 가 지킨다.
# 여기는 **읽기 장치까지**다 — 조건별 방문률과 추세.


def test_the_scene_stops_at_instruments():
    """이 층은 근거를 만들지 않는다. 장면은 통계까지고 고르기는 다음 단이다."""
    scene = build(_planted(), REGIONS, NOW, PROJECTION)
    assert not hasattr(scene, "cards"), "장면이 아직 고르기를 들고 있다"
    assert scene.regions and scene.thresholds


def test_thresholds_travel_with_the_result():
    """잠정 문턱을 숨기지 않는다 — 화면이 "추세" 대신 "표본 부족" 을 말할 수 있게."""
    scene = build(_planted(), REGIONS, NOW, PROJECTION)
    assert scene.thresholds["unexplored_cut"] == UNEXPLORED_CUT
    assert scene.thresholds["recent_days"] == 30
