"""전송 층의 공통부 — 예산·재시도·실패 분류 (RT-001 ① · ⑤-b).

**각 전송이 자기 봉투를 해석하고, 여기는 그 판정을 받아 재시도만 한다.** 봉투를 여기서 열려고
하면 data.go.kr 의 `resultCode` 와 apihub 의 `{result:{status,message}}` 와 kakao 의 `errorType` 이
한 함수 안에 들어오고, 그게 RT-001 ①이 기관 축을 기각한 이유와 같은 모양의 실수가 된다.

**실패가 두 축이라는 것이 이 층의 존재 이유다** (§6.1 함정 2) — data.go.kr 은 **HTTP 200 안에**
`resultCode` 로 실패를 숨기고(`05` 가 그렇다), apihub 는 HTTP 레벨로 죽는다(503·타임아웃).
status code 만 보면 `05` 를 성공으로 오인한다.

**동기다.** crawler 가 동기 httpx 를 쓰고 Celery 워커도 동기라 한 가지 방식으로 맞춘다. 병렬은
호출부의 스레드로 낸다 (⑤-b "provider 는 서로 독립이므로 병렬"). 되돌리는 비용이 낮은 이유는
**파싱이 여기 없기 때문**이다 — provider 는 응답 본문만 받아 파싱하므로(검문소 B 가 실호출 없이
픽스처로 검증하는 것이 그 증거) 비동기로 바뀌어도 갈아엎을 것은 이 파일의 fetch 몇 줄뿐이다.
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from .. import config

# ⑤-b 확정값. 실측은 1회 재시도로 2/2 복구됐고 2회면 여유다.
MAX_RETRIES = 2                     # 총 3시도
BACKOFF_SEC = (0.5, 1.5)            # 1차 → 2차. 지터를 더해 herd 를 흩는다


class TransportError(Exception):
    """전송이 값을 못 가져온 이유. **provider 가 `ProviderResult` 로 옮긴다.**

    `observation.py` 를 import 하지 않는 것은 의도다 — 전송 층이 관측 모델을 알면 층이 뒤집힌다.
    """

    retryable = False

    def __init__(self, reason: str, *, hint: str = "") -> None:
        super().__init__(reason if not hint else f"{reason} — {hint}")
        self.reason = reason
        self.hint = hint


class Unavailable(TransportError):
    """지금은 안 되지만 다시 해 볼 가치가 있다 — `05` · 5xx · 네트워크 · 타임아웃."""

    retryable = True


class Rejected(TransportError):
    """몇 번을 해도 같다 — 4xx · `11` 파라미터 · `12` 경로 없음 · `30` 미신청. 사람이 고칠 일이다."""


class NoData(TransportError):
    """호출은 통했고 값이 없다 (`03`). 실패와 갈라 두는 이유는 **고칠 것이 없기 때문**이다.

    ⑤ 저하 정책은 둘을 똑같이 `UNKNOWN` 으로 만들지만, 로그에서 "신청이 안 됐다"와
    "그 시각 값이 아직 없다"가 섞이면 원인을 못 찾는다.
    """


class Budget:
    """한 요청이 쓸 수 있는 시간 (⑤-b 전체 8초).

    **예산이 ⑤-b 의 핵심이다** — ⑤-a 가 "축 하나가 없어도 답한다"를 만들어 줬으므로 오래
    기다리느니 저하 응답을 주는 것이 낫다. "다 모일 때까지 기다린다"는 저하 응답을 설계해 놓고
    저하될 기회를 안 주는 셈이다.
    """

    def __init__(self, seconds: float | None = None) -> None:
        self.total = config.REQUEST_BUDGET_SEC if seconds is None else seconds
        self._deadline = time.monotonic() + self.total

    def remaining(self) -> float:
        return self._deadline - time.monotonic()

    def expired(self) -> bool:
        return self.remaining() <= 0


# 테스트가 갈아끼울 수 있게 이름으로 둔다 — 백오프를 실제로 자면 테스트가 그만큼 느려진다.
_sleep = time.sleep


def request(
    url: str,
    *,
    classify: Callable[[httpx.Response], TransportError | None],
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    budget: Budget | None = None,
) -> httpx.Response:
    """한 번의 조회. 성공한 응답을 그대로 돌려준다 — **파싱은 provider 몫이다.**

    `classify` 가 이 전송의 봉투를 읽어 실패면 `TransportError` 를, 정상이면 `None` 을 준다.
    재시도 여부는 그 예외의 `retryable` 이 정한다. 재시도할 것과 아닌 것을 여기서 목록으로
    갖지 않는 이유 — 목록이 전송마다 다르고, 여기 모으면 세 봉투를 다 알게 된다.
    """
    budget = budget or Budget()
    last: TransportError | None = None

    for attempt in range(MAX_RETRIES + 1):
        if budget.expired():
            # 남은 provider 를 포기하고 UNKNOWN 으로 응답하는 경로다 (⑤-b).
            raise Unavailable("예산 초과", hint=f"{budget.total}초 안에 못 끝냈다") from last

        try:
            response = httpx.get(
                url,
                params=params,
                headers=headers,
                # 남은 예산보다 긴 개별 타임아웃은 의미가 없다 — 전체 예산이 먼저 끝난다
                timeout=min(config.REQUEST_TIMEOUT_SEC, max(budget.remaining(), 0.1)),
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            # 네트워크·타임아웃. apihub 에서 반복 발생했고 재시도로 성공했다 (⑤-b 실측)
            last = Unavailable(f"{type(exc).__name__}", hint=str(exc))
        else:
            failure = classify(response)
            if failure is None:
                return response
            if not failure.retryable:
                raise failure
            last = failure

        if attempt < MAX_RETRIES:
            wait = BACKOFF_SEC[attempt] * random.uniform(0.8, 1.2)
            if wait < budget.remaining():
                _sleep(wait)

    assert last is not None
    raise last


def http_status_failure(response: httpx.Response) -> TransportError | None:
    """HTTP 레벨만 보는 공통 판정. 봉투를 읽기 **전에** 통과시킨다.

    5xx 는 재시도, 4xx 는 즉시 포기 — 세 전송이 공유하는 유일한 규칙이다.
    """
    if response.status_code >= 500:
        return Unavailable(f"HTTP {response.status_code}")
    if response.status_code >= 400:
        return Rejected(f"HTTP {response.status_code}", hint=response.text[:200])
    return None
