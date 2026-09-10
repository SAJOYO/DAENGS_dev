"""실시간 서비스(Cloud Run)를 부르는 얇은 래퍼 (D-068).

**응답을 해석하지 않는다.** 상태 코드와 JSON 을 그대로 돌려준다 — `/life/walk-conditions`
는 판정 불가일 때 **503 본문에 응답 전체**를 싣고(`daengs_life` 의 `controllers/walk.py`)
프런트가 그것을 읽는다(`ask-inspect.tsx`). 여기서 502 로 뭉개면 콘솔 패널이 조용히 망가진다.

**인증은 Google 서명 ID 토큰이다.** 대상 서비스 URL 이 audience 다. 자격 증명은 VM 의
메타데이터 서버(ADC)에서 오고 키 파일이 없다 — `services/cloudrun_jobs.py` 와 같은 출처이고
클라이언트만 다르다(잡은 `run_v2`, 서비스는 이 토큰).

`daengs_life` 를 import 하지 않는다. 이 파일이 아는 것은 **경로 두 개와 JSON** 뿐이다.
"""
from __future__ import annotations

from typing import Any

import httpx

#: 콜드 부팅(1~2초) + 판정 예산 8초 + 여유. 정적 수집이 도는 콜드 캐시는 이보다 오래
#: 걸릴 수 있는데, 그때는 부르는 쪽이 저하로 닫는다 (finalize 는 `status="failed"`).
TIMEOUT_SEC = 15.0


class RealtimeUnavailable(Exception):
    """전송이 실패했다 — 응답 자체를 못 받았다(토큰을 못 받아 요청을 낼 수조차 없었던

    경우를 포함한다 — ADC 조회도 "응답을 못 받은" 것과 같은 범주다). HTTP 오류 응답은
    여기 안 온다.
    """


#: audience(대상 서비스 URL)별 ID 토큰 자격증명 캐시. **문자열이 아니라 Credentials
#: 객체를 캐시한다** — 문자열로 캐시하면 1시간 뒤 조용히 401 이 나기 시작한다. 이
#: 경로는 자주 돈다(콘솔 패널·어시스턴트 산책 능력·산책 finalize 전부가 지난다) —
#: 매번 메타데이터 서버를 왕복하는 비용을 없애려는 것이다. `cloudrun_jobs.py` 의
#: `_clients` 캐시와 같은 판단이고, 여기서는 API 클라이언트가 아니라 자격증명을 캐시한다.
_credentials: dict[str, Any] = {}


def _id_token(audience: str) -> str:
    """ADC 로 대상 서비스용 ID 토큰을 받는다. audience 별로 자격증명을 캐시해 두고,

    만료됐거나(또는 아직 한 번도 못 받았으면) `refresh` 로 새로 받는다 — `Credentials`
    객체가 만료를 스스로 안다. 테스트가 이 함수 이름을 통째로 갈아끼우는 경우가 많은데,
    캐시·갱신은 이 함수 **안**에 있으므로 그렇게 갈아끼우면 이 로직 자체는 건너뛴다
    (그래서 그 경로를 확인하려면 `google.oauth2.id_token.fetch_id_token_credentials` 를
    갈아끼워야 한다).
    """
    import google.auth.transport.requests
    import google.oauth2.id_token

    request = google.auth.transport.requests.Request()
    creds = _credentials.get(audience)
    if creds is None:
        creds = google.oauth2.id_token.fetch_id_token_credentials(audience, request=request)
        _credentials[audience] = creds
    if not creds.valid:
        creds.refresh(request)
    return creds.token


def _call(method: str, path: str, *, base_url: str, **kwargs: Any) -> tuple[int, Any]:
    root = base_url.rstrip("/")
    try:
        token = _id_token(root)
    except Exception as exc:
        # ADC 조회 자체가 실패한 경우(`google.auth.exceptions.*` — 메타데이터 서버가
        # 안 닿거나 자격증명이 없는 환경). 여기서 안 잡으면 raw 예외가 그대로 새 나가고,
        # Task 4 의 프록시 라우터는 `RealtimeUnavailable` 만 잡아 502 로 바꾸므로 이
        # 경로만 500 이 된다. 원인 문구(예외 타입·메시지)는 그대로 싣는다 — 뭉개면
        # "인증이 문제인지 네트워크가 문제인지"를 로그에서 못 가른다.
        raise RealtimeUnavailable(f"{type(exc).__name__}: {exc}") from exc
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=TIMEOUT_SEC) as client:
            response = client.request(method, f"{root}{path}", headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise RealtimeUnavailable(f"{type(exc).__name__}: {exc}") from exc
    try:
        return response.status_code, response.json()
    except ValueError:
        # JSON 이 아닌 응답(예: IAM 이 막은 403 의 HTML). 본문을 문자열로 실어 보낸다 —
        # 뭉개면 "왜 막혔는지"가 로그에서 사라진다.
        return response.status_code, {"detail": response.text[:512]}


def get_walk(lat: float, lon: float, *, base_url: str) -> tuple[int, Any]:
    return _call("GET", "/life/walk-conditions", base_url=base_url,
                 params={"lat": lat, "lon": lon})


def post_weather_at(payload: dict[str, Any], *, base_url: str) -> tuple[int, Any]:
    return _call("POST", "/weather/at", base_url=base_url, json=payload)


__all__ = ["RealtimeUnavailable", "TIMEOUT_SEC", "get_walk", "post_weather_at"]
