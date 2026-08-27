"""data.go.kr — 기상청 단기예보·특보 · 에어코리아 2종이 공유하는 전송 (RT-001 ①).

다섯 provider 가 이 한 파일을 쓴다. RT-001 ①의 "관찰 2" 가 정확히 이 봉투 처리를 두고 한 말이다 —
데이터 축으로만 자르면 `resultCode` 분기와 `items` 언랩이 다섯 군데로 복사되고 따로 늙는다.

전송 계약 (§6.1 실측)
  인증   `serviceKey` 쿼리 — **Decoding 키** (정규화는 config.normalize_key)
  성공   `response.header.resultCode == "00"` + `response.body.items.item[]`
  실패   HTTP 4xx + `OpenAPI_ServiceResponse.cmmMsgHeader.returnReasonCode`
         **또는 HTTP 200 + resultCode** ← 이쪽이 함정이다
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from .. import config
from .base import Budget, NoData, Rejected, TransportError, Unavailable, http_status_failure, request

# 말하지 않으면 이만큼. **상한이 아니라 기본값이다** — 호출부가 더 크게 줄 수 있다.
#
# 처음에는 이것을 전역 상한으로 두고 "다섯 provider 가 같은 게이트웨이를 공유하니 한 곳의 큰
# 페이지가 그 모듈에 머물지 않는다"고 적었다. **실측이 그 논리를 뒤집었다** (2026-08-25):
#
#   기상청 단기예보 `numOfRows=1000` → 4/4 정상, 871/871건
#   에어코리아 실시간 `numOfRows=1000` → 4번 중 2번 HTTP 504
#
# 두 백엔드가 독립이고, §6.1 함정 3 의 `05` 는 **에어코리아 이야기였다.** 전역으로 100 에
#묶으면 단기예보가 871건 중 100건만 받아 ③-a 의 T+24h 타임라인이 9시간에서 끊긴다 —
# 조용히 짧아지므로 눈으로는 "예보가 좀 적네" 로만 보인다. 그래서 상한을 없애고,
# 페이지를 크게 쓸지는 그 API 를 아는 provider 가 정한다.
DEFAULT_ROWS = 100

# §6.1 함정 2 — 대조 실험(실존+미신청 / 실존+신청됨 / 없는 경로)으로 확정한 표.
# `05` 만 재시도 가치가 있고, 나머지는 몇 번을 해도 같다.
_RESULT_CODES: dict[str, tuple[type[TransportError], str]] = {
    "05": (Unavailable, "SERVICETIMEOUT — 게이트웨이가 백엔드 연결 실패"),
    "03": (NoData, "NO_DATA — 그 조건의 값이 없다"),
    "11": (Rejected, "필수 파라미터 누락 — 우리 코드가 틀렸다"),
    "12": (Rejected, "NO_OPENAPI_SERVICE — 경로가 없다"),
    "30": (Rejected, "SERVICE_KEY_IS_NOT_REGISTERED — 활용신청을 안 했다"),
}


_XML_RESULT_CODE = re.compile(r"<resultCode>\s*(\d+)\s*</resultCode>")


def result_code(response: httpx.Response) -> tuple[str, str] | None:
    """`(resultCode, resultMsg)`. 못 읽으면 `None`. **JSON 이든 XML 이든 읽는다.**

    공개 이름인 이유 — **apihub typ02 가 이 봉투를 그대로 쓴다** (§6.8 ①). 그쪽에서 복사하면
    코드표가 두 벌이 된다.

    JSON 만 볼 수 없는 이유가 실측에 있다 (2026-08-25) — `getMsrstnList` 는 `dataType=JSON` 을
    무시하고 XML 로 **성공**을 돌려준다. `resultCode 00` 에 데이터까지 정상인데 파싱만 실패하므로,
    JSON 만 보면 그 성공을 "봉투를 읽지 못했다"로 읽고 세 번 재시도한 뒤 엉뚱한 이유를 보고한다.
    아래 `get()` 이 포맷 파라미터를 둘 다 보내서 이 경우를 없애지만, 판정은 포맷에 안 기대게 둔다.
    """
    try:
        header = response.json()["response"]["header"]
    except (ValueError, KeyError, TypeError):
        if m := _XML_RESULT_CODE.search(response.text):
            return m.group(1).strip(), ""
        return None
    return str(header.get("resultCode", "")).strip(), str(header.get("resultMsg", ""))


def _classify(response: httpx.Response) -> TransportError | None:
    """HTTP 를 보고, 그다음 봉투 안을 본다. **순서가 뒤바뀌면 `05` 를 성공으로 읽는다.**"""
    failure = http_status_failure(response)
    if failure is not None:
        return failure

    found = result_code(response)
    if found is None:
        # 실패 시 `OpenAPI_ServiceResponse.cmmMsgHeader` 로 돌아오는 폴백이 있다. 코드까지
        # 파고들지 않는 이유 — 여기 오는 것은 이미 비정상이고, 본문을 hint 로 남기면 사람이
        # 읽어서 판정한다. 추측으로 분기하면 틀린 분기가 조용히 굳는다.
        body = response.text[:200]
        if "SERVICE_KEY" in body or "SERVICE KEY" in body:
            return Rejected("서비스키 거부", hint=body)
        return Unavailable("봉투를 읽지 못했다", hint=body)

    code, message = found
    if code == "00":
        return None
    kind, why = _RESULT_CODES.get(code, (Rejected, message))
    return kind(f"resultCode {code or '없음'}", hint=why)


def get(path: str, params: dict[str, Any], *, budget: Budget | None = None) -> Any:
    """조회하고 **`response.body` 를 그대로** 돌려준다.

    `items.item[]` 까지 파고들지 않는 이유 — provider 마다 그 아래 모양이 다르다. 단기예보는
    `items.item[]` 이고 특보는 단건이며, `totalCount`·`numOfRows` 는 페이지 판단에 필요하다.
    전송이 여기서 한 겹 더 벗기면 provider 가 못 보는 정보가 생긴다.
    """
    if not config.DATA_GO_KR_KEY:
        raise Rejected("DATA_GO_KR_KEY 없음",
                       hint="발급·활용신청은 docs/data-sources.md §9. **Decoding 키**를 쓴다")

    query = {
        "serviceKey": config.DATA_GO_KR_KEY,
        # **포맷 파라미터 이름이 계열마다 다르다** (실측 2026-08-25) — 기상청(`1360000`)은
        # `dataType=JSON`, 에어코리아(`B552584`)는 `returnType=json` 이다. 같은 호스트·같은
        # 봉투인데 이름만 갈린다. 둘 다 보낸다 — 모르는 쪽은 무시되고, 다섯 오퍼레이션에서
        # 조합을 확인했다. 호출부가 고르게 하면 provider 마다 이 사실을 알아야 한다.
        "dataType": "JSON",
        "returnType": "json",
        "pageNo": 1,
        "numOfRows": DEFAULT_ROWS,
        **params,
    }
    response = request(f"https://apis.data.go.kr{path}", params=query,
                       classify=_classify, budget=budget)
    try:
        return response.json()["response"]["body"]
    except ValueError as exc:
        # 포맷 파라미터를 둘 다 보냈는데도 JSON 이 아니면 새 계열을 만난 것이다. 조용히
        # 넘기면 provider 가 빈 결과로 읽는다 — 그건 "값이 없다"와 구분되지 않는다.
        raise Unavailable("JSON 을 요청했는데 아니었다",
                          hint=response.text[:200]) from exc
