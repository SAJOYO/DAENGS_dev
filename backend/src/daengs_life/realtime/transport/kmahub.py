"""기상청 API허브 — **한 호스트에 계열이 둘이다** (RT-001 ① 정정 · §6.8).

①은 apihub 를 "텍스트/CSV" 한 줄로 규정했는데 실측은 둘이었다:

  typ01  `/typ01/url/*.php` · `/typ01/cgi-bin/url/*`   텍스트/CSV, `#` 주석 헤더
  typ02  `/typ02/openApi/<서비스>/<오퍼레이션>`          data.go.kr 과 같은 체계의 JSON

**층 결정은 이 정정으로 오히려 강해졌다** — 기상청 하나가 이제 전송 계약 셋(data.go.kr ·
typ01 · typ02)에 걸쳐 있다. 기관 축으로 잘랐다면 `kma.py` 안에 인증 2종·응답 포맷 3종이 들어간다.
두 계열을 한 파일에 두는 것은 **전송 층 안쪽의 문제**라 층 경계를 건드리지 않는다.

전송 계약 (§6.1·§6.8 실측)
  인증   `authKey` 쿼리 — 두 계열 공통
  실패   두 계열 공통으로 `{result:{status,message}}` JSON
         (data.go.kr 의 `cmmMsgHeader` 와도 kakao 의 `errorType` 과도 다르다)
  ⚠️ 활용신청이 **오퍼레이션 단위**다. `LivingWthrIdxServiceV3` 아래인데도 `getSenTaIdxV3` 는
     승인, `getUVIdxV3` 는 403 이다. data.go.kr 이 서비스 단위로 받는 것과 다르다 (§6.8 ①)
"""
from __future__ import annotations

from typing import Any

import httpx

from .. import config
from . import datagokr
from .base import Budget, NoData, Rejected, TransportError, http_status_failure, request

BASE = "https://apihub.kma.go.kr"

# §6.8 ② — AWS 매분자료의 결측. 헤더 주석이 "−50 이하면 관측이 없거나 결측"이라고 명시한다.
# 전송 층에 두는 이유는 이 값이 **파일 포맷의 약속**이지 어느 관측량의 성질이 아니기 때문이다.
MISSING = -99.9


def _classify(response: httpx.Response) -> TransportError | None:
    """실패 봉투가 두 계열 공통이라 한 함수로 끝난다.

    성공이 텍스트인 계열이 있어서 **본문이 JSON 이면 일단 실패를 의심한다** — 성공 응답은
    `#` 로 시작하는 주석 헤더거나 typ02 의 정상 JSON 이다.
    """
    failure = http_status_failure(response)
    if failure is not None:
        return failure

    # HTTP 200 인데 `{result:{status,...}}` 가 오는 경우를 잡는다. typ01 의 정상 응답은
    # 텍스트라 여기서 ValueError 가 나고, 그것이 곧 정상 신호다.
    try:
        payload = response.json()
    except ValueError:
        return None

    if isinstance(payload, dict) and "result" in payload:
        result = payload["result"] or {}
        status = str(result.get("status", ""))
        message = str(result.get("message", ""))
        if status and status != "200":
            kind = Rejected if status.startswith("4") else NoData
            return kind(f"apihub status {status}", hint=message)
        return None

    # **typ02 는 실패도 data.go.kr 봉투로 낸다** (실측 2026-08-25). §6.1 은 실패 봉투가
    # 두 계열 공통으로 `{result:{status,message}}` 라고 적었는데, 그건 인증·경로 실패의
    # 이야기였다. 호출이 통한 뒤의 `03 NO_DATA` 는 `{response:{header:{resultCode}}}` 로 온다 —
    # `result` 만 보면 **값 없음이 성공으로 통과**하고 provider 가 빈 body 를 파싱하게 된다.
    found = datagokr.result_code(response)
    if found is None or found[0] == "00":
        return None
    code, message = found
    kind, why = datagokr._RESULT_CODES.get(code, (Rejected, message))
    return kind(f"resultCode {code}", hint=why)


def get_text(path: str, params: dict[str, Any], *, budget: Budget | None = None) -> str:
    """typ01 — 텍스트/CSV. **`r.text` 를 쓰면 안 된다** (§6.8 ②).

    `help=1` 로 주석 헤더를 받으면 한글이 **EUC-KR** 이라 httpx 의 자동 디코딩이 깨뜨린다.
    바이트를 직접 디코딩한다. `errors="replace"` 인 이유 — 헤더 한 글자가 깨졌다고 관측값
    한 덩어리를 통째로 버리는 것은 손해다. 값은 ASCII 숫자라 영향을 안 받는다.
    """
    response = request(f"{BASE}{path}", params={**params, "authKey": _key()},
                       classify=_classify, budget=budget)
    return response.content.decode("euc-kr", errors="replace")


def get_json(path: str, params: dict[str, Any], *, budget: Budget | None = None) -> Any:
    """typ02 — data.go.kr 과 같은 체계의 JSON."""
    response = request(f"{BASE}{path}", params={**params, "authKey": _key()},
                       classify=_classify, budget=budget)
    return response.json()


def _key() -> str:
    if not config.KMA_HUB_KEY:
        raise Rejected("KMA_HUB_KEY 없음",
                       hint="활용신청은 **오퍼레이션 단위**다 (§6.8 ①). 발급처는 docs/data-sources.md §9")
    return config.KMA_HUB_KEY


def rows(text: str) -> list[list[str]]:
    """typ01 텍스트를 열 목록으로. `#` 주석과 빈 줄을 걷어낸다.

    파싱을 여기까지만 하는 이유 — **열의 의미는 오퍼레이션마다 다르다.** `nph-aws2_min` 의
    9번째 열이 기온(`TA`)이라는 것은 그 provider 의 지식이지 전송의 지식이 아니다.
    """
    return [line.split() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def value(raw: str) -> float | None:
    """결측(`-99.9` 이하)을 `None` 으로. §6.8 ② 의 헤더 주석이 근거다.

    `-50` 이하 전부가 결측 표기라 등호가 아니라 부등호로 본다 — 실측에서 `-99.9` 만 봤지만
    헤더가 규정한 것은 구간이고, `-99` 나 `-999` 가 오는 날 조용히 영하 999도가 되면 안 된다.
    """
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return None if number <= -50.0 else number
