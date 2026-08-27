"""설정 → 제공사 인스턴스. 정적지도·지오코딩·모드별 경로를 각각 다른 제공사로 꽂을 수 있다.

**여기는 사용량 게이트를 모른다.** 게이트를 씌운 조립은 `app/usage/composition.py` 에 있다.
`usage.metered` 가 이미 `providers.base` 계약을 알고 있으므로 조립을 그쪽에 두면 화살표가
`usage → providers` 한 방향으로 남는다. 여기서 씌우면 두 패키지가 서로를 import 한다
(결정 #67 §3).
"""

from functools import lru_cache

from app.core.config import settings
from app.providers.base import MapProvider, Mode, NullProvider
from app.providers.fake import FakeProvider
from app.providers.kakao import KakaoProvider
from app.providers.naver import NaverProvider
from app.providers.tmap import TmapProvider


def build_raw_provider(name: str) -> MapProvider:
    """사용량 게이트가 씌워지지 않은 raw provider를 만든다.

    실제 유료 호출 경로는 `app.usage.composition`에서 이 결과를 게이트로 감싼 뒤 사용한다.
    """
    if name == "fake":
        return FakeProvider()
    if name == "kakao" and settings.kakao_rest_key:
        return KakaoProvider(settings.kakao_rest_key)
    if name == "naver" and settings.naver_ncp_key_id and settings.naver_ncp_key:
        return NaverProvider(settings.naver_ncp_key_id, settings.naver_ncp_key)
    if name == "tmap" and settings.tmap_app_key:
        return TmapProvider(settings.tmap_app_key)
    return NullProvider()


@lru_cache
def static_map_provider() -> MapProvider:
    return build_raw_provider(settings.static_map_provider or settings.map_provider)


@lru_cache
def geocode_provider() -> MapProvider:
    return build_raw_provider(settings.geocode_provider or settings.map_provider)


def route_provider_name(mode: Mode) -> str:
    """설정값 그대로. 'none' 은 **이 수단을 안 쓴다**는 뜻이지 추정하라는 뜻이 아니다."""
    return {"walk": settings.walk_route_provider,
            "car": settings.car_route_provider,
            "transit": settings.transit_route_provider}[mode]
