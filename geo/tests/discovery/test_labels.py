"""사용자 어휘의 경계값. `diff` 와 `features/hospital/actions` 가 공유한다.

간접으로는 이미 덮여 있다 — 두 소비자의 테스트가 문장 전체를 비교하므로 라벨이 틀리면
거기서 깨진다. 여기서 따로 고정하는 것은 **경계 하나**다: `format_distance_m` 이 m 과 km
사이에서 갈리는 지점. 소비자 테스트는 대표값 하나씩만 쓰기 때문에 999/1000 근처가 바뀌어도
안 걸린다.
"""

from app.discovery.refine.labels import format_distance_m, value_label


def test_distance_switches_to_km_at_1000m():
    assert format_distance_m(999) == "999m"
    assert format_distance_m(1000) == "1km"


def test_km_drops_trailing_zeros():
    """`%g` 라 2.0km 가 아니라 2km 다. 버튼에 들어가는 문자열이라 자릿수가 곧 폭이다."""
    assert format_distance_m(2000) == "2km"
    assert format_distance_m(1500) == "1.5km"


def test_unknown_value_falls_back_to_str():
    """어휘에 없는 값이 와도 라벨이 비지 않는다 — 빈 버튼이 제일 나쁘다."""
    assert value_label("walk") == "도보"
    assert value_label("made_up") == "made_up"
