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
    """전송이 실패했다 — 응답 자체를 못 받았다. HTTP 오류 응답은 여기 안 온다."""


def _id_token(audience: str) -> str:
    """ADC 로 대상 서비스용 ID 토큰을 받는다. 테스트가 이 이름을 갈아끼운다."""
    import google.auth.transport.requests
    import google.oauth2.id_token

    return google.oauth2.id_token.fetch_id_token(
        google.auth.transport.requests.Request(), audience
    )


def _call(method: str, path: str, *, base_url: str, **kwargs: Any) -> tuple[int, Any]:
    root = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {_id_token(root)}"}
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
