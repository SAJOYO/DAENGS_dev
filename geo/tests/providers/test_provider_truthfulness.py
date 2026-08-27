"""제공사 경로의 **진실성 계약**. 숫자가 어디서 왔는지가 응답에 남아야 한다.

**왜 필요한가**: 실측 호출이 실패하면 조용히 FakeProvider 로 갈아탔다. 라벨(`source`)은
바뀌었지만 시설(횡단보도·계단·지하보도)까지 따라와서, TMAP 이 죽은 날 노령·관절견의 경로에
**실측된 적 없는 "계단 1회 — 노령" 경고**가 붙었다. 결정 #21 이 "폴백은 시간·거리만,
틀린 시설정보는 없는 것보다 나쁨" 이라 못박은 지점을 폴백 자신이 위반하고 있었다.

계약: 모르는 것을 아는 것처럼 만들지 않는다.
"""

from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import settings
from app.discovery.facts import RuntimeFacts
from app.discovery.resolver import resolve_request
from app.discovery.state import EditableState
from app.journey.engine import snapshot
from app.profile.source import PERSONAS
from app.providers.base import LatLng
from app.providers.tmap import TmapProvider
from app.usage.composition import route_capability_problems, route_provider

O, D = LatLng(37.4979, 127.0276), LatLng(37.5145, 127.0316)
NOW = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def shipped_defaults(monkeypatch):
    """이 파일은 **출고 기본값의 계약**을 본다 — 이 개발자의 `.env` 가 아니라.

    `settings` 는 `.env` 를 읽는 전역이라, 로컬에 `DAENGS_WALK_ROUTE_PROVIDER=tmap` 이나
    `DAENGS_MAP_PROVIDER=naver` 가 있으면 이 파일 전체가 붉어졌다. 남의 기계에서만
    깨지는 테스트는 계약을 못 지킨다 — 아무도 안 믿게 되니까.

    `Settings.model_fields` 의 선언 기본값으로 되돌려 놓고 시작한다. 특정 설정을 보는
    테스트는 그 위에 자기가 monkeypatch 한다.
    """
    for name in ("walk_route_provider", "car_route_provider", "transit_route_provider",
                 "map_provider", "static_map_provider", "dev_console", "tmap_app_key"):
        monkeypatch.setattr(settings, name, type(settings).model_fields[name].default)
    route_provider.cache_clear()
    yield
    route_provider.cache_clear()


async def _walk(measured: bool, profile=PERSONAS["halmae"]):
    facts = RuntimeFacts(now=NOW, profile=profile)
    plan = resolve_request(EditableState(lat=O.lat, lng=O.lng), facts,
                           kind=None, companion="dog", measured=measured).journey
    return (await snapshot(plan, D)).walk


# ------------------------------------------------------------------ status 축
async def test_preview_is_labelled_estimate_with_a_reason():
    """목록 미리보기는 **의도한** 추정이다 — 강등과 구분돼야 한다."""
    leg = await _walk(measured=False)
    assert leg.status == "estimate" and leg.status_reason == "preview"
    assert leg.min and leg.m, "추정이라도 거리·시간은 준다 — 그건 모델이고 라벨이 붙어 있다"


async def test_measured_request_on_a_fake_provider_is_still_an_estimate():
    """`walk_route_provider=fake` 로 실측을 요청해도 measured 가 아니다.

    계산식을 제공사 결과라고 부르는 게 이 계약이 없애려는 거짓말이다. 그리고 이유가
    `preview` 와 달라야 한다 — 의도한 추정과 대역 사용은 다른 사건이다.
    """
    leg = await _walk(measured=True)
    assert leg.status == "estimate" and leg.status_reason == "provider_is_fake"


async def test_measured_request_without_a_key_degrades_and_says_why(monkeypatch):
    """실측을 요청했는데 제공사가 없으면 추정으로 내려간다. **왜 추정인지가 남는다.**"""
    monkeypatch.setattr(settings, "walk_route_provider", "tmap")
    monkeypatch.setattr(settings, "tmap_app_key", "")
    route_provider.cache_clear()
    try:
        leg = await _walk(measured=True)
    finally:
        route_provider.cache_clear()
    assert leg.status == "estimate" and leg.status_reason == "provider_unconfigured"
    assert leg.status_reason != "preview", "설정 오류가 의도한 추정으로 위장됐다"


async def test_estimate_never_carries_facilities_or_route_shape():
    """추정 leg 는 시설과 선을 싣지 않는다. 둘 다 실측에서만 나오는 값이다."""
    leg = await _walk(measured=False)
    assert leg.facilities is None and leg.road_mix is None
    assert leg.polyline is None


async def test_estimate_advice_never_warns_about_facilities():
    """할매(노령+관절)에게 실측 없이 계단·지하도 경고를 붙이면 안 된다.

    시간·기온 사유는 남는다 — 그건 프로필과 거리에서 나오지 시설에서 나오지 않는다.
    """
    leg = await _walk(measured=False)
    banned = ("지하 통로", "횡단")
    assert not [w for w in leg.why if any(b in w for b in banned)], leg.why


async def test_estimate_does_not_explain_a_comparison_it_never_made():
    """추정에서는 옵션 비교를 안 한다 — 큰길 비율을 모르는데 "골목으로 골랐다"고 할 수 없다.

    프로필·거리에서 나오는 사유(권장 시간 초과)는 남는다. 그건 비교의 산물이 아니다.
    """
    leg = await _walk(measured=True)          # 기본 설정은 fake → 실측 불가
    assert not [w for w in leg.why if "골목" in w or "큰길" in w], leg.why
    assert any("권장" in w for w in leg.why), "프로필 사유까지 사라지면 과교정이다"


# ------------------------------------------------- none = 안 쓴다, 추정하라가 아니다
async def test_disabled_mode_is_unavailable_not_invented(monkeypatch):
    """`transit_route_provider=none` 은 **이 수단을 안 쓴다**는 뜻이다.

    예전엔 none 으로 꺼도 폴백이 돌아 1,500원짜리 대중교통 leg 가 나왔다 — 설정이
    거짓말이 됐다. 이제 숫자가 없다 (0 이 아니라 null).
    """
    monkeypatch.setattr(settings, "transit_route_provider", "none")
    facts = RuntimeFacts(now=NOW, profile=PERSONAS["halmae"])   # 소형견 → transit 노출
    plan = resolve_request(EditableState(lat=O.lat, lng=O.lng), facts,
                           kind=None, companion="dog", measured=False).journey
    leg = (await snapshot(plan, D)).transit
    assert leg is not None, "수단 자체는 해당된다 — 해당 없음(None)과 데이터 없음은 다르다"
    assert leg.status == "unavailable" and leg.status_reason == "provider_disabled"
    assert leg.min is None and leg.m is None and leg.fare is None


# ------------------------------------------------------------ 시작 시 능력 검증
def test_capability_check_passes_for_the_default_config():
    assert route_capability_problems() == []


def test_capability_check_catches_a_provider_that_cannot_serve_the_mode(monkeypatch):
    """`car_route_provider=kakao` 는 장애가 아니라 **평시에도 100% 추정**이었다.

    KakaoProvider.route 가 자동차 미구현이라 언제나 None 을 준다. 런타임 강등과 같은
    침묵 경로로 합류하면 구분할 수 없으니 시작할 때 세운다.
    """
    monkeypatch.setattr(settings, "car_route_provider", "kakao")
    monkeypatch.setattr(settings, "kakao_rest_key", "k")
    route_provider.cache_clear()
    try:
        problems = route_capability_problems()
    finally:
        route_provider.cache_clear()
    assert len(problems) == 1 and "car" in problems[0]


def test_capability_check_catches_a_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "walk_route_provider", "tmap")
    monkeypatch.setattr(settings, "tmap_app_key", "")
    route_provider.cache_clear()
    try:
        problems = route_capability_problems()
    finally:
        route_provider.cache_clear()
    assert len(problems) == 1 and "walk" in problems[0]


def test_declared_capability_matches_what_the_provider_implements():
    """선언과 구현이 어긋나면 검증이 통과하면서 런타임에 None 이 온다."""
    assert TmapProvider.route_modes == frozenset({"walk"})


@pytest.mark.parametrize("mode", ["car", "transit"])
async def test_tmap_refuses_modes_it_did_not_declare(mode):
    assert await TmapProvider("k").route(mode, O, D) is None


async def test_tmap_request_asks_for_the_recommended_route_explicitly():
    """`searchOption: 0` 을 **명시해서** 보낸다 — 생략하지 않는다.

    Decision: #66

    지금 TMAP 기본값이 0 이라 생략해도 결과는 같다. 그래도 명시하는 이유는, 우리 정책이
    "옵션을 안 쓴다" 가 아니라 **"추천 경로 하나를 쓴다"** 이기 때문이다. 생략하면 그 의미를
    외부 서비스의 기본값에 위임하게 되고, 저쪽이 언젠가 기본값을 바꾸면 우리 동작이 조용히
    따라 바뀐다. 요청 본문만 봐도 무엇을 요구했는지 보여야 한다.
    """
    sent: dict = {}

    class _Capture:
        async def post(self, url, json, headers, params):
            sent.update(json)
            raise httpx.HTTPError("여기까지만 — 파싱은 이 테스트의 관심이 아니다")

    provider = TmapProvider("k", client=_Capture())
    with pytest.raises(httpx.HTTPError):
        await provider.route("walk", O, D)

    assert sent["searchOption"] == 0

