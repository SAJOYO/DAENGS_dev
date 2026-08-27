"""근거와 고르기 계약. `app/features/territory/evidence.py`.

[evidence-layer] 원칙 다섯 중 둘이 여기서 지켜진다.

    원칙 2   장치는 값만 내면 안 된다 — 분자·분모·비교 기준·표본량을 달고 다닌다
    원칙 5   고르기도 장치다 — 후보 전부 + 무엇이 왜 뽑혔는지

**정답을 심은 자료에서는 무엇이 제일 말할 가치 있는지 우리가 안다.** 그래서 선택 정책도
테스트할 수 있다 — 이게 "고르기도 장치다" 의 실제 값어치다.

[evidence-layer]: ../../docs/explorations/walk/evidence-layer.md
"""

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.features.territory.evidence import (
    MIN_DELTA,
    STATE_WEIGHT,
    brief,
    choose,
    gather,
    rank,
    sentence,
)
from app.features.territory.experience import NamedRegion, build, region_stats
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


def _box(region_id: str, x0: float, x1: float, name: str) -> NamedRegion:
    dlat = math.degrees(120.0 / EARTH_R)
    return NamedRegion(Region(id=region_id, version=1,
                              ring=((LAT - dlat, _east(x0)), (LAT - dlat, _east(x1)),
                                    (LAT + dlat, _east(x1)), (LAT + dlat, _east(x0)))),
                       name)


NORTH = _box("north", 200.0, 900.0, "양재천 북쪽")
PARK = _box("park", 1_400.0, 2_100.0, "도곡공원")
SOUTH = _box("south", 2_600.0, 3_300.0, "단지 남쪽")
FAR = _box("far", 3_800.0, 4_500.0, "가본 적 없는 블록")
REGIONS = [NORTH, PARK, SOUTH, FAR]


def _walk(walk_id: str, at: datetime, x0: float, x1: float):
    step = 1.0 if x1 >= x0 else -1.0
    xs = [x0 + step * i for i in range(int(abs(x1 - x0)) + 1)]
    fixes = [WalkFix(client_seq=i, chain_index=0, at=at + timedelta(seconds=i),
                     lat=LAT, lng=_east(x), accuracy_m=3.0, is_mock=False)
             for i, x in enumerate(xs)]
    segs = compute_facts("w", "d", fixes[0].at, fixes[-1].at + timedelta(seconds=1),
                         fixes).segments
    return paint_sheet(walk_id, at, segs, RADIUS_U, NARROW_STEP)


def _planted():
    """정답을 심은 자료 — **무엇이 제일 말할 가치 있는지 우리가 안다.**

        저녁 20 회 북쪽 · 아침 20 회 공원   → 조건 편향은 있지만 안 변한다
        남쪽: 40~49 일 전에만 10 회        → **최근 뚝 끊겼다. 이게 오늘의 뉴스다**
        먼 블록: 0 회                      → 언제나 참인 사실. 뉴스가 아니다
    """
    sheets = []
    for i in range(20):
        day = NOW - timedelta(days=i + 1)
        sheets.append(_walk(f"eve{i}", day.replace(hour=18), 250.0, 850.0))
        sheets.append(_walk(f"morn{i}", day.replace(hour=8), 1_450.0, 2_050.0))
    for i in range(10):
        day = NOW - timedelta(days=40 + i)
        sheets.append(_walk(f"south{i}", day.replace(hour=18), 2_650.0, 3_250.0))
    return sheets


def _stats(sheets=None, now=NOW):
    return [region_stats(sheets if sheets is not None else _planted(), r, now, PROJECTION)
            for r in REGIONS]


# ---- 원칙 2 — 근거는 자기 측정 문맥을 달고 다닌다 --------------------------------------


def test_every_evidence_carries_its_numerator_denominator_and_baseline():
    """비율만 들고 다니면 "선호합니다" 가 12/15 인지 120/300 인지 아무도 모른다."""
    for item in gather(_stats(), "evening"):
        assert item.cohort.selected >= 0 and item.cohort.visited >= 0
        assert item.cohort_label, item.kind
        if item.kind == "unexplored":
            assert item.baseline is None, "견줄 것이 없으면 없다고 해야 한다"
        else:
            assert item.baseline is not None and item.baseline_label
            assert item.delta is not None


def test_evidence_says_which_region_version_it_judged():
    """면이 바뀌면 어제의 답이 오늘의 폴리곤 때문에 조용히 뜻이 바뀐다 (#59 와 같은 처리)."""
    for item in gather(_stats(), "evening"):
        assert item.region_version == 1


# ---- Evidence 생성 ---------------------------------------------------------------------


def test_one_region_can_produce_several_evidences():
    """"저녁에 유난히 간다" 와 "최근 뜸해졌다" 는 같은 곳의 서로 다른 사실이고 둘 다 참이다."""
    found = gather(_stats(), "evening")
    by_region: dict[str, set[str]] = {}
    for item in found:
        by_region.setdefault(item.region_id, set()).add(item.kind)
    assert len(by_region["south"]) >= 2, f"남쪽이 근거 하나만 냈다: {by_region['south']}"


def test_a_region_never_visited_gets_unexplored_with_no_baseline():
    kinds = {i.kind for i in gather(_stats(), "evening") if i.region_id == "far"}
    assert "unexplored" in kinds


def test_no_evidence_at_all_when_there_is_no_history():
    """기록이 0 이면 근거도 0 이다. "여긴 안 가보셨네요" 는 안 간 게 아니라 우리가 모르는 것."""
    assert gather(_stats([]), "evening") == []


# ---- 원칙 5 — 고르기도 장치다 ----------------------------------------------------------


def test_the_policy_picks_the_fact_we_planted_as_the_news():
    """**이 파일의 핵심.** 심은 자료의 정답은 남쪽 하락이고, 정책이 그걸 골라야 한다.

    오늘 사실은 여럿이다 — 북쪽 저녁 편향도 참이고, 먼 블록 미방문도 참이다. 그중
    **지금 말할 가치가 있는 것**은 뚝 끊긴 남쪽이다.
    """
    picked = choose(rank(gather(_stats(), "evening"), "evening"))
    assert picked is not None
    assert picked.evidence.region_id == "south"
    assert picked.evidence.kind == "visit_drop"


def test_every_candidate_comes_back_not_just_the_winner():
    """탈락한 것도 이유를 달고 나온다 — "왜 저 말은 안 했지" 가 검산돼야 한다."""
    ranked = rank(gather(_stats(), "evening"), "evening")
    assert len(ranked) == len(gather(_stats(), "evening"))
    for row in ranked:
        assert row.reasons, row.evidence.kind
        if not row.sayable:
            assert row.dropped in {"표본 부족", "차이가 작다"}


def test_sayable_candidates_sort_ahead_of_dropped_ones():
    ranked = rank(gather(_stats(), "evening"), "evening")
    flags = [row.sayable for row in ranked]
    assert flags == sorted(flags, reverse=True), "탈락한 것이 말할 수 있는 것보다 앞에 왔다"


def test_a_thin_sample_is_gated_not_merely_scored_down():
    """**관문은 점수가 아니다.**

    표본이 얇은 것을 낮은 점수로 깎기만 하면 큰 변화가 얇은 표본을 이겨 버린다.
    `1/2 → 2/2` 로 "두 배 늘었어요" 라고 말하는 푸시가 정확히 그렇게 나온다.
    """
    # 최근 창에 남쪽을 **안 간** 산책 하나, 앞 창에 간 산책 하나 → 0/1 대 1/1 로
    # 추세는 −1.0 인데 표본이 각 1 회다. 크기만 보면 오늘 제일 큰 변화다.
    thin = [_walk("a", NOW - timedelta(days=3), 250.0, 850.0),
            _walk("b", NOW - timedelta(days=40), 2_650.0, 3_250.0)]
    ranked = rank(gather(_stats(thin), "evening"), "evening")
    drops = [r for r in ranked if r.evidence.kind in ("visit_drop", "visit_rise")]
    assert drops, "추세 근거가 아예 안 생기면 이 테스트가 뜻이 없다"
    assert all(r.dropped == "표본 부족" for r in drops)
    assert choose(ranked) is None, "얇은 표본만 있는데 뭔가를 말했다"


def test_a_small_difference_is_not_worth_saying():
    ranked = rank(gather(_stats(), "evening"), "evening")
    for row in ranked:
        if row.evidence.kind != "unexplored" and row.sayable:
            assert row.evidence.magnitude >= MIN_DELTA


def test_unexplored_loses_to_a_real_change():
    """"여긴 안 가보셨죠" 는 언제나 참이라 뉴스가 아니다. 진짜 변화가 있으면 거기 진다."""
    ranked = rank(gather(_stats(), "evening"), "evening")
    sayable = [r for r in ranked if r.sayable]
    kinds = [r.evidence.kind for r in sayable]
    if "unexplored" in kinds and len(set(kinds)) > 1:
        assert kinds.index("unexplored") > 0


def test_the_context_changes_which_evidence_wins():
    """"지금 조건" 이 순위를 바꾼다 — 안 그러면 알잘딱이 아니라 그냥 목록이다."""
    stats = _stats()
    evening = rank(gather(stats, "evening"), "evening")
    morning = rank(gather(stats, "morning"), "morning")

    def bias_score(ranked, region_id):
        return next((r.score for r in ranked
                     if r.evidence.kind == "condition_bias"
                     and r.evidence.region_id == region_id), 0.0)

    # 북쪽은 저녁에만, 공원은 아침에만 편향 근거가 선다.
    # 반대쪽이 0 인 이유: **아래로 벌어진 것은 근거로 안 만든다.** 북쪽의 아침 편향은
    # −0.40 이라 |차이| 로 재면 저녁의 +0.27 을 이기는데, "아침엔 여기 안 가시죠" 는
    # "오늘 어디 갈까" 에 답하는 표면에서 쓸 말이 아니다. 테스트를 짜다가 걸렸다.
    assert bias_score(evening, "north") > 0 and bias_score(morning, "north") == 0.0
    assert bias_score(morning, "park") > 0 and bias_score(evening, "park") == 0.0


def test_ranking_is_deterministic():
    stats = _stats()
    first = [(r.evidence.region_id, r.evidence.kind, r.score)
             for r in rank(gather(stats, "evening"), "evening")]
    second = [(r.evidence.region_id, r.evidence.kind, r.score)
              for r in rank(gather(stats, "evening"), "evening")]
    assert first == second


# ---- 브리핑 ----------------------------------------------------------------------------


def test_a_briefing_carries_the_choice_the_alternatives_and_the_thresholds():
    out = brief(build(_planted(), REGIONS, NOW, PROJECTION, context_chip="evening"))
    assert out.chosen is not None and out.chosen.evidence.region_id == "south"
    assert len(out.candidates) > 1
    assert out.thresholds["min_delta"] == MIN_DELTA
    assert out.thresholds["trend_min_walks"] == 5


def test_saying_nothing_is_a_valid_outcome():
    """말할 게 없으면 아무 말도 안 한다. 억지로 고르면 그게 틀린 푸시가 된다."""
    out = brief(build([], REGIONS, NOW, PROJECTION, context_chip="evening"))
    assert out.chosen is None and out.candidates == []


def test_every_sayable_kind_has_a_sentence():
    """템플릿이 빠진 kind 가 있으면 화면에서 KeyError 로 터진다."""
    seen = set()
    for row in rank(gather(_stats(), "evening"), "evening"):
        if row.sayable:
            assert sentence(row), row.evidence.kind
            seen.add(row.evidence.kind)
    assert len(seen) >= 2, f"kind 가 하나뿐이면 커버리지가 없다: {seen}"


# ---- 리뷰에서 나온 것 ------------------------------------------------------------------


def test_condition_bias_gates_both_sides_not_just_the_cohort():
    """**표본 관문은 양쪽을 다 본다.**

    처음엔 조건 쪽만 봤다. 그러면 `저녁 5/5` 대 `그 외 0/1` 이 통과한다 — "저녁이 유난하다"
    를 떠받치는 **반대쪽 표본이 1 회**인데도. 추세는 처음부터 두 창을 다 봤는데 여기만
    안 보고 있었고, 이 PR 이 스스로 세운 "관문" 원칙과 정면으로 어긋났다.
    """
    # 저녁 5 회 전부 북쪽, 그 외 1 회는 북쪽 아님 → 저녁 5/5 대 그 외 0/1
    sheets = [_walk(f"eve{i}", (NOW - timedelta(days=i + 1)).replace(hour=18),
                    250.0, 850.0) for i in range(5)]
    sheets.append(_walk("day0", (NOW - timedelta(days=6)).replace(hour=13),
                        1_450.0, 2_050.0))

    bias = [e for e in gather(_stats(sheets), "evening")
            if e.kind == "condition_bias" and e.region_id == "north"]
    assert bias, "조건 편향 근거 자체가 안 생기면 이 테스트가 뜻이 없다"
    item = bias[0]
    assert (item.cohort.visited, item.cohort.selected) == (5, 5)
    assert (item.baseline.visited, item.baseline.selected) == (0, 1), "비교군이 '그 외' 여야 한다"
    assert item.trustworthy is False, "반대쪽 표본이 1 회인데 믿을 만하다고 했다"
    assert all(not r.sayable for r in rank([item], "evening"))


def test_the_baseline_is_the_complement_not_the_whole():
    """"저녁 편향" 이 견주는 것은 `저녁` 대 `전체` 가 아니라 `저녁` 대 `저녁 외` 다.

    전체 안에 저녁이 들어 있어서, 전체와 견주면 저녁이 **스스로를 희석한 값**과 견주게 된다.
    """
    item = next(e for e in gather(_stats(), "evening")
                if e.kind == "condition_bias" and e.region_id == "north")
    overall = next(s for s in _stats() if s.region_id == "north").by_chip["all"]
    assert item.baseline.selected == overall.selected - item.cohort.selected
    assert item.baseline.visited == overall.visited - item.cohort.visited
    assert "외" in item.baseline_label


def test_a_standing_pattern_loses_to_actual_news():
    """**상태는 매일 다시 참이라 매일 다시 울린다.**

    북쪽이 저녁에만 가는 것은 1 년 내내 참이라 저녁마다 같은 말을 하게 된다. 남쪽이 뚝
    끊긴 것은 오늘 새로 참이 된 사실이다.

    실제로 이렇게 뒤집혔었다 — 북쪽 편향은 **지금 조건의 근거**라 관련도 1.0 을 받고
    (0.667 × 1.0 = 0.667), 남쪽 하락은 조건 밖이라 0.6 으로 깎여서(1.0 × 0.6 = 0.600)
    **상태가 뉴스를 이겼다.** 뉴스 가중치가 그걸 되돌린다.
    """
    ranked = rank(gather(_stats(), "evening"), "evening")
    top = ranked[0]
    assert top.evidence.kind == "visit_drop"
    assert top.evidence.region_id == "south"

    bias = next(r for r in ranked if r.evidence.kind == "condition_bias"
                and r.evidence.region_id == "north")

    def before_news(row):
        return row.reasons["magnitude"] * row.reasons["relevance"]

    assert before_news(bias) > before_news(top), "뉴스 가중치가 없으면 상태가 이겼어야 한다"
    assert bias.score < top.score, "가중치를 넣으면 뉴스가 이겨야 한다"
    assert bias.reasons["is_news"] is False
    assert bias.reasons["state_weight"] == STATE_WEIGHT
    assert top.reasons["is_news"] is True


def test_a_dropped_evidence_gets_no_sentence():
    """말하지 않기로 한 근거로는 문장을 만들지 않는다.

    붙여 두면 소비자가 `sayable` 검사를 한 번 빠뜨리는 순간 그대로 거짓 푸시가 된다.
    Judgment 가 "말하면 안 돼" 라고 정해 놓고 Surface 가 이미 말을 만들어 둔 상태를
    아예 못 만들게 한다.
    """
    thin = [_walk("a", NOW - timedelta(days=3), 250.0, 850.0),
            _walk("b", NOW - timedelta(days=40), 2_650.0, 3_250.0)]
    dropped = [r for r in rank(gather(_stats(thin), "evening"), "evening") if not r.sayable]
    assert dropped, "탈락한 근거가 없으면 이 테스트가 뜻이 없다"
    for row in dropped:
        with pytest.raises(ValueError, match="말하지 않기로"):
            sentence(row)
