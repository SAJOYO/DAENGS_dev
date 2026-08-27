from datetime import UTC, datetime

from app.discovery.facts import RuntimeFacts
from app.discovery.resolver import resolve_request
from app.discovery.state import EditableState
from app.geo.tagging import dog_ok, tags_for
from app.journey.advice import walk_advice
from app.profile.source import PERSONAS
from app.providers.base import LatLng
from app.providers.fake import FakeProvider
from app.providers.tmap import parse_tmap
from tests.conftest import route


def test_tags_from_names():
    assert tags_for("강남24시동물의료센터") == ["24h", "center"]
    assert not dog_ok(tags_for("강남고양이전문병원"))
    assert dog_ok(tags_for("역삼동물병원"))


def test_specialty_words_in_a_name_are_not_tags():
    """과목은 어휘가 아니다 (#64). 간판에 '안과'가 있어도 자격이 아니라 상호다.

    한국 수의 진료에 전문의 제도가 없어서 이 단어들은 검증되지 않은 마케팅 문구다.
    """
    assert tags_for("역삼동물안과") == []
    assert tags_for("서초동물정형외과") == []
    assert tags_for("강남재활동물병원") == []


def test_advice_says_nothing_about_stairs_anymore():
    """계단 판정은 #66 으로 없앴다 — 288경로에서 0회였다.

    시설을 넣어도 계단으로는 아무 말도 안 나온다. 노령+관절인 할매로 본다 —
    예전에 이 조합이 `avoid` 를 내던 자리다.
    """
    lvl, why = walk_advice(route(10, stairs=1), PERSONAS["halmae"], None)
    assert not [w for w in why if "계단" in w], why
    assert lvl == "ok"


def test_advice_young_dog_ok_long_walk():
    assert walk_advice(route(30), PERSONAS["kong"], None)[0] == "ok"


def test_advice_brachy_caution_over_cap():
    lvl, _ = walk_advice(route(25), PERSONAS["dubu"], None)
    assert lvl == "caution"


def test_advice_user_max_min():
    assert walk_advice(route(12), None, 10)[0] == "avoid"


def test_advice_underpass_still_warns_for_large_dogs():
    """지하보도는 288경로 중 6% 에 실재한다 — 재료가 있으니 판정도 남는다.

    계단(0/288)과 갈리는 지점이다. 없는 것은 걷어내고 있는 것은 지킨다.
    """
    lvl, why = walk_advice(route(5, underpass=1, underpass_m=40), PERSONAS["janggun"], None)
    assert lvl == "caution" and any("지하 통로" in w for w in why), why


async def test_fake_route_gives_numbers_but_never_facilities():
    """추정은 거리·시간·요금까지다. **시설은 지어내지 않는다** (결정 #21, #38).

    예전엔 "400m마다 횡단보도 1 · 1.5km 넘으면 계단 1" 을 만들어 냈고, 그게 walk_advice 로
    흘러 노령견 경로에 실측된 적 없는 계단 경고를 붙였다.
    """
    f = FakeProvider()
    o, d = LatLng(37.4979, 127.0276), LatLng(37.5145, 127.0316)
    a = await f.route("walk", o, d)
    assert a.facilities is None
    assert a.distance_m > 0 and a.duration_s > 0
    c = await f.route("car", o, d)
    assert c.taxi_fare and c.taxi_fare >= 4800


def test_parse_tmap_counts_facilities_like_real_response():
    # 실측 형태: 횡단보도=포인트 211/212, 지하보도=연속 LineString facilityType 14 (하나의 통로), 계단=포인트 127
    L = lambda ft, m: {"properties": {"facilityType": ft, "distance": m},
                       "geometry": {"type": "LineString", "coordinates": [[127.0, 37.5], [127.01, 37.51]]}}
    P = lambda tt: {"properties": {"turnType": tt}, "geometry": {"type": "Point", "coordinates": [127.0, 37.5]}}
    data = {"features": [
        {"properties": {"totalDistance": 2306, "totalTime": 1880, "turnType": 200}, "geometry": {"type": "Point", "coordinates": [127.0, 37.5]}},
        L("14", 18), P(12), L("14", 66), P(12), L("14", 26),      # 지하 통로 하나 (110m), 안에서 좌회전 2번
        P(211), L("15", 10), L("11", 300),
        P(127), L("11", 50),                                       # 계단
        P(212), L("15", 12), L("17", 651),                          # 17은 무시
        L("11", 200), L("14", 40),                                  # 별개 지하 통로
        P(201),
    ]}
    r = parse_tmap(data)
    assert r.distance_m == 2306 and r.duration_s == 1880
    f = r.facilities
    assert f.crosswalk == 2 and f.stairs == 1
    assert f.origin_passage_m == 110          # 출발 직후 역 통로 — 장애물 아님
    assert f.underpass == 1 and f.underpass_m == 40
    assert len(r.polyline) == 2 * 10


def test_dog_time_factor_orders_personas():
    from app.journey.advice import dog_time_factor
    f_kong, f_dubu, f_hal = (dog_time_factor(PERSONAS[k]) for k in ("kong", "dubu", "halmae"))
    assert f_kong < f_dubu < f_hal <= 2.0
    assert dog_time_factor(None) == 1.2


# ------------------------------------------------- 수단별 적용 범위 (계층 불변식)
async def test_walk_only_settings_change_the_walk_leg_only():
    """도보 설정은 도보 leg 에만 닿는다. Transport 가 셋을 다 반환하므로
    '차량 모드인데 도보 판정이 있다'는 정상 — 문제는 **범위가 새는 것**이다.

    축은 `walk.max_walk_min` 이다. `walk.avoid` 로 보던 것을 #66 이후 이걸로 본다."""
    from app.journey.engine import snapshot
    from app.providers.base import LatLng as LL

    o, d = LL(37.4979, 127.0276), LL(37.5145, 127.0316)
    facts = RuntimeFacts(now=datetime(2026, 8, 21, 3, 0, tzinfo=UTC), profile=PERSONAS["halmae"])
    plain_state = EditableState(lat=o.lat, lng=o.lng)
    capped_state = EditableState(lat=o.lat, lng=o.lng)
    capped_state.journey.walk.max_walk_min = 1
    plain_plan = resolve_request(
        plain_state, facts, kind=None, companion="dog", measured=False,
    ).journey
    capped_plan = resolve_request(
        capped_state, facts, kind=None, companion="dog", measured=False,
    ).journey
    plain = await snapshot(plain_plan, d)
    capped = await snapshot(capped_plan, d)

    assert (plain.car.min, plain.car.m, plain.car.advice, plain.car.why) == \
           (capped.car.min, capped.car.m, capped.car.advice, capped.car.why), "차량 leg 가 변했다"
    # 설정은 도보 plan 에만 실린다 — 범위가 새지 않는다
    assert capped_plan.walk.max_walk_min == 1 and plain_plan.walk.max_walk_min is None
    # 도보 쪽에서는 실제로 달라져야 한다 — 안 그러면 이 테스트가 아무것도 안 본다
    assert capped.walk.why != plain.walk.why and capped.walk.advice == "avoid"
    assert plain.walk.status == "estimate" and plain.walk.facilities is None


def test_arrive_note_is_injected_not_hardcoded():
    """공용 route 층은 '진료' 같은 도메인 어휘를 몰라야 한다. 산책·약국이 같은 층을 쓴다."""
    from app.journey.spots import DEFAULT_ARRIVE_NOTE, spot_note
    from app.providers.base import LatLng as LL
    from app.providers.base import Spot

    sp = Spot("arrive", LL(37.5, 127.0), 0, "도착")
    assert "진료" not in DEFAULT_ARRIVE_NOTE, "공용 기본값에 병원 어휘가 있다"
    assert spot_note(sp, None)[0] == DEFAULT_ARRIVE_NOTE
    assert spot_note(sp, None, "도착 — 처방전 챙기기")[0] == "도착 — 처방전 챙기기"


def test_hospital_feature_owns_its_arrive_note():
    from app.features.hospital.api import ARRIVE_NOTE
    assert "진료" in ARRIVE_NOTE
