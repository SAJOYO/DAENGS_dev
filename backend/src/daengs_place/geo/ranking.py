"""선호 태그의 의미 규칙과 부스트 순위 규칙 — **두 진입 경로가 같은 것을 쓴다.**

이슈 #24: `geo/search.py`와 `planning/resolver.py`가 각자 prefer 를 조립하고 있었고,
그 결과 같은 조건이 엔드포인트마다 다른 뜻이 됐다 —
`/hospital`에서는 실제 순위 정책, `/places`·`/pharmacy`에서는 `prefer_hit` 표시용
메타데이터(정렬에 아무 영향 없음)였다. `night=true`를 넣어도 순서가 그대로였다.

정책 조립은 도메인별로 달라도 된다 (약국은 얇고, 병원은 resolver 를 탄다).
**같은 개념의 해석과 순위 반영 방식만** 여기 한 곳에 둔다.
"""

from collections.abc import Callable, Sequence

# 부스트가 순서를 바꿀 수 있는 폭. 결정 #20 — '살짝 위'까지고 거리·시간을 뒤집지 않는다.
DISTANCE_BAND_M = 500
DURATION_BAND_MIN = 5

# 야간·응급은 간판 이름 정규식이 전부다 (name-tagging.md). 신뢰도가 낮아서 순위만 바꾸고
# 결과 집합은 안 건드린다. 같은 재료였던 과목 축은 원천이 없어 #64 로 없앴다.
NIGHT_TAGS = ("night", "24h", "emergency")
EMERGENCY_TAGS = ("emergency", "24h")


def preference_tags(*, night: bool = False, emergency: bool = False) -> tuple[str, ...]:
    """조건 → 선호 태그. 두 진입 경로가 이 함수만 쓴다 (이슈 #24).

    상황 정책(예: 긴급도가 emergency 를 켠다)은 여기 없다 — 호출자가 결정해서 불 값으로
    넘긴다. 이 함수는 '무엇을 선호로 볼 것인가'의 **의미**만 안다.
    """
    prefer: set[str] = set()
    if night:
        prefer.update(NIGHT_TAGS)
    if emergency:
        prefer.update(EMERGENCY_TAGS)
    return tuple(sorted(prefer))


def facility_preference_tags(
    *, parking: bool = False, dog_exclusive: bool = False
) -> tuple[str, ...]:
    """시설 선호 조건 → 선호 태그. `preference_tags` 의 시설판이다.

    시설 축은 병원의 `tags TEXT[]` 같은 배열 컬럼이 아니라 개별 불 컬럼이라, 같은 부스트
    기계(`prefer_boost` · `band_boost_sorted`)를 쓰려고 태그 어휘로 바꾼다.

    무엇이 이 불을 켜는가(비가 와서 실내, 차로 가서 주차, 개가 다른 개를 무서워해서 전용)는
    호출자가 정한다 — 위 `preference_tags` 와 같은 경계다. 여기는 의미만 안다.
    """
    prefer = []
    if parking:
        prefer.append("parking")
    if dog_exclusive:
        prefer.append("dog_exclusive")
    return tuple(prefer)


def prefer_boost(prefer_hit: Sequence[str]) -> int:
    """선호 태그 적중 → 부스트 점수. 근거(evidence) 가산은 호출자가 더한다."""
    return len(prefer_hit) * 2


def band_of(primary: float, band_size: int) -> int:
    return int(primary // band_size)


def rank_key(*, primary: float, boost: int, band_size: int) -> tuple:
    """(밴드, -부스트, 원값). 밴드 안에서만 부스트가 순서를 바꾼다."""
    return (band_of(primary, band_size), -boost, primary)


def band_boost_sorted[T](
    items: list[T],
    *,
    distance_of: Callable[[T], int],
    boost_of: Callable[[T], int],
    band_m: int = DISTANCE_BAND_M,
) -> list[T]:
    """거리 밴드 안에서 부스트로만 순서를 바꾼 목록. 밴드를 넘는 역전은 없다."""
    return sorted(
        items,
        key=lambda it: rank_key(primary=distance_of(it), boost=boost_of(it), band_size=band_m),
    )
