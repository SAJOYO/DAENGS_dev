"""한 산책 안에 관측 공백이 있을 때 붓이 그 공백을 어떻게 다루나.

`docs/explorations/walk/session-continuity-and-dwell.md` §6 이 명시적으로 요구한 테스트다.

    서로 다른 derived continuity chain 사이에는 paint interpolation 을 만들지 않는다.

같은 문서 §7 의 비대칭이 근거다 — 걸었는데 못 칠한 것(false negative)은 다음 산책에서 다시
칠하면 되지만, **안 걸은 곳을 칠하면 없던 공간 경험이 영속 파생에 들어간다.**

## 여기서 가르는 두 가지

    보간         공백을 가로지르는 Segment 를 만드나        — **절대 안 된다**
    붓 겹침      공백 양쪽 붓이 만나 시각적으로 이어지나    — 붓 폭 · 격자 크기의 성질이다

둘은 다르다. 보간은 관측하지 않은 이동을 지어내는 것이고, 붓 겹침은 각 관측점 주변을
정당하게 칠한 결과가 우연히 만나는 것이다. 전자는 금지고 후자는 **재서 알아 두는 것**이다 —
지도에서는 둘 다 "이어져 보이는" 같은 모양이 되기 때문에.

그래서 이 파일의 제목은 "붓이 공백을 넘지 않는다" 가 아니다. **붓은 넘는다** — 40m 안팎까지.
넘지 않는 것은 Segment 다.

## 페르소나 실험이 못 본 자리

`spike_persona_year` 가 만든 2,514 산책은 전부 단일 chain 왕복이다. 그래서 회수 실험도
뷰어 실험도 이 경계를 한 번도 건드리지 않았다. 여기서 따로 고정한다.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.features.territory.paint import NARROW_STEP, BrushProfile, paint_sheet, stack
from app.features.territory.region import Region, region_encounters
from app.features.walk.facts import MAX_GAP_S, MAX_JUMP_M, Segment, compute_facts
from app.features.walk.models import WalkFix
from app.geo.cells import cell_size_m, hex_cell

EARTH_R = 6_371_000.0
LAT, LNG = 37.4979, 127.0276
START = datetime(2026, 7, 1, 9, tzinfo=UTC)
RADIUS_U = 15.0
LEG_M = 40


def _at(x_m: float) -> tuple[float, float]:
    """원점에서 동쪽으로 `x_m` 미터."""
    return LAT, LNG + math.degrees(x_m / (EARTH_R * math.cos(math.radians(LAT))))


def _split_walk(gap_m: float, *, gap_s: float = 1.0,
                explicit_chain: bool = True, bad_accuracy: bool = False):
    """0~40m 를 걷고, 공백을 둔 뒤 그 너머에서 40m 를 더 걷는 한 세션.

    공백을 만드는 방법을 인자로 고른다 — 명시적 pause(client chain), 60초 초과 gap,
    200m 초과 jump, 정확도 거부. 넷 다 `compute_facts` 가 Segment 를 끊는 사유다.
    """
    fixes: list[WalkFix] = []
    seconds = 0.0

    def push(x_m: float, chain: int, accuracy: float = 3.0) -> None:
        nonlocal seconds
        lat, lng = _at(x_m)
        fixes.append(WalkFix(client_seq=len(fixes), chain_index=chain,
                             at=START + timedelta(seconds=seconds),
                             lat=lat, lng=lng, accuracy_m=accuracy, is_mock=False))
        seconds += 1.0

    for x in range(LEG_M + 1):
        push(float(x), 0)

    if bad_accuracy:                       # 거부되는 점 하나가 그 자리에서 연속을 끊는다
        push(LEG_M + gap_m / 2, 0, accuracy=999.0)
    seconds += gap_s

    second_chain = 1 if explicit_chain else 0
    for x in range(LEG_M + 1):
        push(LEG_M + gap_m + x, second_chain)

    ended = fixes[-1].at + timedelta(seconds=1)
    return compute_facts("s", "d", START, ended, fixes)


def _gap_cells(gap_m: float, radius_u: float = RADIUS_U) -> set:
    """공백 구간을 촘촘히 훑은 셀들. 셀 크기의 1/4 간격이면 빠뜨리지 않는다."""
    step = max(0.5, cell_size_m(radius_u, LAT) / 4)
    count = max(2, int(gap_m / step))
    return {hex_cell(*_at(LEG_M + gap_m * i / count), radius_u) for i in range(count + 1)}


# ---- 보간이 없다 (구조적 보장) ---------------------------------------------------------


def _x_of(fix: WalkFix) -> float:
    """fix 의 동쪽 거리(m). 어느 다리에 속한 점인지 보려고 되돌린다."""
    return math.radians(fix.lng - LNG) * EARTH_R * math.cos(math.radians(LAT))


def test_no_segment_ever_crosses_a_break():
    """**이 파일의 핵심.** 공백이 아무리 커도 그것을 가로지르는 Segment 가 안 생긴다.

    거리 상한만 보면 약하다 — 공백 10m 짜리 위조 Segment 는 어떤 느슨한 상한도 통과한다.
    그래서 **양 끝이 같은 다리에 있는지**를 본다. 하나라도 공백을 걸치면 실패다.
    """
    for gap in (10.0, 50.0, 200.0, 1000.0):
        computed = _split_walk(gap)
        assert len(computed.segments) == 2 * LEG_M
        assert len({s.chain_index for s in computed.segments}) == 2
        for seg in computed.segments:
            ax, bx = _x_of(seg.a), _x_of(seg.b)
            first = ax <= LEG_M + 1e-6 and bx <= LEG_M + 1e-6
            second = ax >= LEG_M + gap - 1e-6 and bx >= LEG_M + gap - 1e-6
            assert first or second, (
                f"공백 {gap:.0f}m 를 걸친 Segment: {ax:.1f}m → {bx:.1f}m")


def test_a_forged_bridging_segment_would_fill_the_gap():
    """위 테스트가 무엇을 막고 있는지 — 손으로 이어 붙이면 실제로 공백이 메워진다.

    구현을 검사하는 것이 아니라 **검사가 헛돌지 않음**을 보이는 테스트다. 공백을 가로지르는
    Segment 하나를 인위로 넣으면 붓이 그 위를 칠하고, 지도에서 없던 이동이 생긴다.
    """
    gap = 200.0
    computed = _split_walk(gap)
    left_end = max(computed.segments, key=lambda s: _x_of(s.b))
    right_start = min(computed.segments, key=lambda s: _x_of(s.a))
    forged = Segment(a=left_end.b, b=right_start.a, dt=1.0, dist=gap,
                     offset_m=left_end.offset_m, moving=True,
                     chain_index=left_end.chain_index)

    honest = paint_sheet("honest", START, computed.segments, RADIUS_U, NARROW_STEP)
    bridged = paint_sheet("forged", START, [*computed.segments, forged],
                          RADIUS_U, NARROW_STEP)
    holes = _gap_cells(gap)
    assert len(holes & set(honest.occupancy)) < len(holes) / 2
    assert holes <= set(bridged.occupancy), "위조 Segment 를 넣었는데도 공백이 남았다"


def test_every_break_reason_splits_the_chain():
    """chain 변경 · gap · jump · 정확도 거부 — 넷 다 같은 경계를 만든다."""
    cases = {
        "chain": _split_walk(30.0, explicit_chain=True),
        "gap": _split_walk(30.0, gap_s=MAX_GAP_S + 30, explicit_chain=False),
        "jump": _split_walk(MAX_JUMP_M + 50, explicit_chain=False),
        "accuracy": _split_walk(30.0, explicit_chain=False, bad_accuracy=True),
    }
    for why, computed in cases.items():
        chains = {s.chain_index for s in computed.segments}
        assert len(chains) == 2, f"{why} 가 연속을 안 끊었다: chain {chains}"


def test_both_sides_are_still_painted():
    """공백이 있다고 양쪽 관측까지 잃으면 안 된다 — 끊는 것과 버리는 것은 다르다."""
    computed = _split_walk(200.0)
    sheet = paint_sheet("w", START, computed.segments, RADIUS_U, NARROW_STEP)
    assert hex_cell(*_at(LEG_M / 2), RADIUS_U) in sheet.occupancy          # 앞 구간
    assert hex_cell(*_at(LEG_M + 200 + LEG_M / 2), RADIUS_U) in sheet.occupancy  # 뒤 구간


def test_a_wide_gap_leaves_a_hole_in_the_paint():
    """붓이 닿지 못할 만큼 넓은 공백은 지도에서도 비어 있다."""
    gap = 200.0
    computed = _split_walk(gap)
    sheet = paint_sheet("w", START, computed.segments, RADIUS_U, NARROW_STEP)
    painted = _gap_cells(gap) & set(sheet.occupancy)
    assert len(painted) < len(_gap_cells(gap)) / 2, "넓은 공백이 메워졌다"


# ---- 붓 겹침은 붓 폭과 격자 크기의 성질이다 (재서 알아 둔다) ----------------------------


def test_a_narrow_gap_is_visually_bridged_by_brush_width():
    """공백이 붓 도달의 2 배보다 좁으면 양쪽 붓이 만나 **끊김이 안 보인다.**

    보간이 아니다 — Segment 는 여전히 둘로 갈려 있고(위 테스트), 각 관측점 주변을
    정당하게 칠한 결과가 만나는 것뿐이다. 다만 화면에서는 이어져 보이므로 사실로 고정한다.
    """
    narrow = NARROW_STEP.reach_m                       # 20m
    computed = _split_walk(narrow)                     # 도달 × 1 — 확실히 만난다
    sheet = paint_sheet("w", START, computed.segments, RADIUS_U, NARROW_STEP)
    assert _gap_cells(narrow) <= set(sheet.occupancy)
    # 그래도 chain 은 둘이다 — 시각적 연결과 자료의 연결은 별개다
    assert len({s.chain_index for s in computed.segments}) == 2


def _unbroken(gap_m: float, radius_u: float, profile) -> bool:
    """공백 구간의 셀이 하나도 안 빠지고 칠해졌나 — 화면에서 이어져 보이나."""
    computed = _split_walk(gap_m)
    sheet = paint_sheet("w", START, computed.segments, radius_u, profile)
    return _gap_cells(gap_m, radius_u) <= set(sheet.occupancy)


def _bridging_boundary(radius_u: float, profile) -> int:
    """이어져 보이는 **최대** 공백(m). 1m 눈금으로 훑어 마지막으로 이어진 값."""
    last = 0
    for gap in range(1, 121):
        if _unbroken(float(gap), radius_u, profile):
            last = gap
    return last


@pytest.mark.parametrize(("radius_u", "expected"), [(8.0, 41), (15.0, 44), (30.0, 66)])
def test_the_bridging_boundary_holds_at_each_cell_size(radius_u: float, expected: int):
    """이어져 보이는 최대 공백은 셀 크기마다 다르고, 그 값을 여기 고정한다.

    2026-08-26 실측값이다. 붓 도달은 셋 다 20m 인데 경계는 41 → 44 → 66m 로 벌어진다 —
    **셀이 클수록 여유가 커진다.** 공백 양 끝의 큰 셀 하나가 붓과 공백을 함께 삼켜서,
    실제로는 안 닿는 자리도 "칠해진 셀" 로 올라오기 때문이다.

    그래서 이 여유는 붓의 성질이 아니라 **저장 격자의 성질**이다. 격자를 키우는 변경은
    보간을 만들지 않고도 화면상 끊김을 지운다 — 그게 여기 숫자로 박혀 있어야 하는 이유다.
    """
    assert _bridging_boundary(radius_u, NARROW_STEP) == expected


def test_the_bridging_boundary_tracks_the_brush_not_the_grid():
    """경계가 격자만의 성질은 아니다 — 같은 격자에서 붓을 넓히면 같이 벌어진다.

    도달 20m 붓과 25m 붓을 같은 15 단위 격자에서 잰다. 위 테스트가 격자 쪽 기여를
    고정하고, 이 테스트가 붓 쪽 기여를 고정한다. 둘이 같이 있어야 "도달 × 2 + 셀 여유"
    라는 읽기가 성립한다.
    """
    wider = BrushProfile("이진 25m", (25.0,), (1.0,))
    assert wider.reach_m > NARROW_STEP.reach_m
    narrow_edge = _bridging_boundary(RADIUS_U, NARROW_STEP)
    wide_edge = _bridging_boundary(RADIUS_U, wider)
    assert wide_edge == 50
    assert wide_edge - narrow_edge == pytest.approx(
        2 * (wider.reach_m - NARROW_STEP.reach_m), abs=cell_size_m(RADIUS_U, LAT)
    ), "붓 도달 차이의 2 배만큼 경계가 움직여야 한다"


# ---- 소비자들도 경계를 본다 ------------------------------------------------------------


def test_stack_keeps_the_hole_across_sheets():
    """겹치기에서도 공백은 메워지지 않는다 — 같은 공백을 가진 장을 여러 번 겹쳐도."""
    gap = 200.0
    sheets = [
        paint_sheet(f"w{i}", START + timedelta(days=i),
                    _split_walk(gap).segments, RADIUS_U, NARROW_STEP)
        for i in range(5)
    ]
    canvas = stack(sheets)
    painted = _gap_cells(gap) & set(canvas)
    assert len(painted) < len(_gap_cells(gap)) / 2, "장을 겹치니 공백이 메워졌다"


def test_region_encounter_does_not_merge_across_a_break():
    """면 판정도 공백을 넘어 한 번의 체류로 합치지 않는다.

    공백 양쪽이 같은 면 안이면, 자료상 두 번의 진입이어야 한다 — 한 번으로 합치면
    "그 안에 계속 있었다" 가 되어 관측하지 않은 시간을 체류로 만든다.
    """
    gap = 200.0
    computed = _split_walk(gap)
    half = (2 * LEG_M + gap) / 2
    corners = []
    for dx, dy in ((-50, -60), (half * 2 + 50, -60), (half * 2 + 50, 60), (-50, 60)):
        lat = LAT + math.degrees(dy / EARTH_R)
        lng = LNG + math.degrees(dx / (EARTH_R * math.cos(math.radians(LAT))))
        corners.append((lat, lng))
    wide = Region(id="park", version=1, ring=tuple(corners))

    found = region_encounters(computed.segments, [wide])
    assert len(found) == 2, f"공백 양쪽이 한 번의 진입으로 합쳐졌다: {len(found)}건"
    assert [e.occurrence_index for e in found] == [0, 1]
