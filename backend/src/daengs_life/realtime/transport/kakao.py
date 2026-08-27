"""카카오 Local — 행정동 이름 표기 (RT-001 ①).

**이 전송이 없어도 관통된다.** 좌표 변환도 최근접 측정소도 `geo.py` 가 자체 계산하므로
(§6.5), 카카오에 남은 역할은 "○○동" 이라는 **사람이 읽을 표기**뿐이다. 그래서 키가 없으면
조용히 `Rejected` 를 내고 ⑤ 저하가 좌표 표기로 답하게 둔다.

전송 계약 (§6.1·§6.2 실측)
  인증   `Authorization: KakaoAK <REST키>` **헤더** — 셋 중 유일하게 쿼리가 아니다
  성공   `documents[]` + `meta`
  실패   HTTP 403 + `{errorType, message}`
  ⚠️ REST 키만으로는 403 이다. 앱의 `제품 설정 > 카카오맵` **활성화**가 필요하고,
     반대로 리다이렉트 URI·허용 IP·플랫폼 등록은 불필요하다 (§6.2)
"""
from __future__ import annotations

from typing import Any

import httpx

from .. import config
from .base import Budget, Rejected, TransportError, http_status_failure, request


def _classify(response: httpx.Response) -> TransportError | None:
    """카카오는 실패를 HTTP 로 낸다 — 봉투 안에 숨기지 않는다."""
    if response.status_code == 403:
        # 403 의 원인이 둘이라 구분해 준다. 키가 맞는데도 막히는 쪽이 §6.2 가 반나절을 쓴 함정이다.
        try:
            message = response.json().get("message", "")
        except ValueError:
            message = response.text[:200]
        return Rejected("HTTP 403", hint=f"{message} · 앱의 `제품 설정 > 카카오맵` 활성화를 확인할 것")
    return http_status_failure(response)


def get(path: str, params: dict[str, Any], *, budget: Budget | None = None) -> Any:
    """조회하고 JSON 을 그대로 돌려준다."""
    if not config.KAKAO_REST_KEY:
        raise Rejected("KAKAO_REST_KEY 없음",
                       hint="동 이름 표기만 못 한다 — 판정은 좌표로 계속된다 (RT-001 ①)")

    response = request(f"https://dapi.kakao.com{path}", params=params,
                       headers={"Authorization": f"KakaoAK {config.KAKAO_REST_KEY}"},
                       classify=_classify, budget=budget)
    return response.json()
