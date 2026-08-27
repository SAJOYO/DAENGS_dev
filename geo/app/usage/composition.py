"""사용량 게이트가 필요한 제공사 호출을 조립한다.

`providers.registry` 는 설정에서 raw provider를 고르기만 하고 게이트를 모른다. 게이트를 씌우는
일이 여기 있는 이유는 화살표 때문이다 — `usage.metered` 는 `providers.base` 계약을 알아야
래핑할 수 있으므로 `usage → providers` 는 이미 있는 방향이고, 조립을 반대편(`providers`)에
두면 두 패키지가 서로를 import 한다. 실제로 그랬다 (결정 #67 §3).

이 파일은 최상위 composition root가 아니다. 현재 `journey.engine` 같은 도메인이 이 조립을
직접 당긴다. 도메인이 주입받는 구조로 바뀌는 별도 결정 전까지는 metered provider 경로의
조립점으로만 둔다.
"""

from functools import lru_cache

from app.providers.base import MapProvider, Mode
from app.providers.naver import NaverProvider
from app.providers.registry import build_raw_provider, route_provider_name, static_map_provider
from app.usage.metered import MeteredRouteProvider, MeteredStaticMapFetcher, StaticMapFetcher
from app.usage.registry import usage_gate


@lru_cache
def static_map_fetcher() -> StaticMapFetcher | None:
    provider = static_map_provider()
    if isinstance(provider, NaverProvider):
        return MeteredStaticMapFetcher(provider, usage_gate())
    return None


@lru_cache
def route_provider(mode: Mode) -> MapProvider:
    provider = build_raw_provider(route_provider_name(mode))
    if provider.name in ("none", "fake"):
        return provider
    return MeteredRouteProvider(provider, usage_gate())


def route_cache_stats() -> dict:
    size = 0
    for mode in ("walk", "car", "transit"):
        provider = route_provider(mode)
        if isinstance(provider, MeteredRouteProvider):
            size += provider.cache_size()
    return {"size": size}


def route_capability_problems() -> list[str]:
    """설정한 제공사가 그 모드를 실제로 구현했나. 반환 = 사람이 읽는 문제 목록.

    `car_route_provider=kakao` 는 장애가 아니라 **평시에도 100% 추정**이었다 —
    KakaoProvider.route 가 자동차 미구현이라 언제나 None 을 준다. 설정 오류가 런타임
    강등과 같은 침묵 경로로 합류하면 구분할 방법이 없으니, 시작할 때 갈라둔다.
    """
    problems: list[str] = []
    for mode in ("walk", "car", "transit"):
        name = route_provider_name(mode)
        if name in ("none", "fake"):
            continue
        provider = route_provider(mode)
        if provider.name == "none":
            problems.append(f"{mode}: '{name}' 제공사 키가 없다 (DAENGS_*_KEY 확인)")
        elif mode not in provider.route_modes:
            supported = ", ".join(sorted(provider.route_modes)) or "없음"
            problems.append(f"{mode}: '{name}' 는 이 모드를 구현하지 않았다 (구현: {supported})")
    return problems
