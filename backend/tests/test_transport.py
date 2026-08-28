"""전송 층 — 봉투 해석과 재시도 (RT-001 구현 계획 4 · ⑤-b).

실호출은 하지 않는다. 여기서 확인하는 것은 **§6.1·§6.8 이 실측한 봉투를 코드가 같게 읽는가**이고,
그 판정은 응답 객체만 있으면 끝난다. 네트워크를 타면 테스트가 그날의 게이트웨이 상태를 따라간다.
"""
from __future__ import annotations

import httpx
import pytest

from daengs_life.realtime import config
from daengs_life.realtime.transport import base, datagokr, kakao, kmahub
from daengs_life.realtime.transport.base import Budget, NoData, Rejected, Unavailable


def _envelope(code: str, message: str = "") -> httpx.Response:
    header = {"resultCode": code, "resultMsg": message}
    return httpx.Response(200, json={"response": {"header": header, "body": {}}})


# --------------------------------------------------------------- data.go.kr

def test_result_code_05_hides_inside_http_200() -> None:
    """§6.1 함정 2 의 핵심 — status code 만 보면 `05` 를 성공으로 읽는다."""
    response = _envelope("05", "SERVICETIMEOUT_ERROR")
    assert response.status_code == 200            # HTTP 로는 멀쩡하다
    failure = datagokr._classify(response)
    assert isinstance(failure, Unavailable) and failure.retryable


@pytest.mark.parametrize(("code", "kind", "retryable"), [
    ("05", Unavailable, True),      # 게이트웨이 일시 실패 — 실측 2/2 복구
    ("03", NoData, False),          # 값이 없다. 고칠 것이 없다
    ("11", Rejected, False),        # 우리 파라미터가 틀렸다
    ("12", Rejected, False),        # 경로가 없다
    ("30", Rejected, False),        # 활용신청을 안 했다
])
def test_datagokr_failure_codes(code: str, kind: type, retryable: bool) -> None:
    """§6.1 함정 2 의 대조 실험 표를 그대로 박는다."""
    got = datagokr._classify(_envelope(code))
    assert isinstance(got, kind)
    assert got.retryable is retryable


def test_datagokr_result_code_00_is_success() -> None:
    assert datagokr._classify(_envelope("00")) is None


def test_datagokr_unknown_code_is_not_retried() -> None:
    """표에 없는 코드는 재시도하지 않는다 — 모르는 실패를 세 번 반복해도 얻는 것이 없다."""
    got = datagokr._classify(_envelope("99", "처음 보는 것"))
    assert isinstance(got, Rejected) and "처음 보는 것" in str(got)


def test_datagokr_xml_fallback_is_not_mistaken_for_success() -> None:
    """실패 시 JSON 이 아니라 XML 로 돌아오는 폴백이 있다 (RT-001 ① 관찰 2)."""
    xml = ("<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>"
           "SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>")
    got = datagokr._classify(httpx.Response(200, text=xml))
    assert isinstance(got, Rejected)


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def fake_request(url, *, classify, params=None, headers=None, budget=None):
        seen.update(params or {})
        return httpx.Response(200, json={"response": {"body": {"items": {"item": []}}}})

    monkeypatch.setattr(datagokr, "request", fake_request)
    monkeypatch.setattr(config, "DATA_GO_KR_KEY", "x" * 88)
    return seen


def test_rows_default_when_the_caller_says_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture(monkeypatch)
    datagokr.get("/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
                 {"stationName": "강남대로"})
    assert seen["numOfRows"] == datagokr.DEFAULT_ROWS == 100
    assert seen["serviceKey"] == "x" * 88


def test_a_caller_that_needs_a_big_page_gets_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """전역 상한이었다면 단기예보가 871건 중 100건만 받아 T+24h 가 9시간에서 끊긴다.

    실측(2026-08-25)이 두 백엔드를 갈랐다 — 기상청 1000행은 4/4 정상, 에어코리아는 4번 중 2번 504.
    §6.1 함정 3 은 에어코리아 이야기였다.
    """
    seen = _capture(monkeypatch)
    datagokr.get("/1360000/VilageFcstInfoService_2.0/getVilageFcst", {"numOfRows": 1000})
    assert seen["numOfRows"] == 1000


def test_both_format_parameter_names_are_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """기상청은 `dataType=JSON`, 에어코리아는 `returnType=json` — 같은 호스트에서 이름이 갈린다."""
    seen = _capture(monkeypatch)
    datagokr.get("/whatever", {})
    assert seen["dataType"] == "JSON" and seen["returnType"] == "json"


def test_a_successful_xml_envelope_is_not_read_as_a_failure() -> None:
    """`getMsrstnList` 는 `dataType=JSON` 을 무시하고 XML 로 **성공**을 준다 (실측 2026-08-25).

    JSON 만 보면 그 성공을 "봉투를 읽지 못했다"로 읽고 세 번 재시도한 뒤 엉뚱한 이유를 보고한다.
    """
    xml = "<response><header><resultCode>00</resultCode></header><body/></response>"
    assert datagokr._classify(httpx.Response(200, text=xml)) is None
    xml05 = "<response><header><resultCode>05</resultCode></header></response>"
    assert isinstance(datagokr._classify(httpx.Response(200, text=xml05)), Unavailable)


def test_datagokr_without_a_key_says_which_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATA_GO_KR_KEY", "")
    with pytest.raises(Rejected, match="Decoding"):
        datagokr.get("/whatever", {})


# -------------------------------------------------------------------- kakao

def test_kakao_403_points_at_the_real_cause() -> None:
    """§6.2 — REST 키가 맞아도 `제품 설정 > 카카오맵` 이 꺼져 있으면 403 이다."""
    got = kakao._classify(httpx.Response(403, json={"errorType": "AccessDeniedError",
                                                    "message": "app disabled"}))
    assert isinstance(got, Rejected) and "카카오맵" in got.hint


def test_kakao_success_passes() -> None:
    assert kakao._classify(httpx.Response(200, json={"documents": [], "meta": {}})) is None


def test_kakao_without_a_key_degrades_rather_than_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """키가 없어도 관통된다는 RT-001 ① 이 메시지에 남아 있어야 한다."""
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "")
    with pytest.raises(Rejected, match="판정은 좌표로 계속"):
        kakao.get("/v2/local/geo/coord2regioncode.json", {})


# ------------------------------------------------------------------- apihub

def test_apihub_text_success_is_not_read_as_failure() -> None:
    """typ01 의 정상 응답은 텍스트다 — JSON 파싱 실패가 곧 정상 신호다."""
    assert kmahub._classify(httpx.Response(200, text="#START7777\n202608241735 108 30.9\n")) is None


def test_apihub_failure_envelope_is_shared_by_both_families() -> None:
    """`{result:{status,message}}` 는 data.go.kr 의 cmmMsgHeader 와도 kakao 와도 다르다 (§6.8)."""
    got = kmahub._classify(httpx.Response(200, json={"result": {"status": "401",
                                                                "message": "허용되지 않은 키"}}))
    assert isinstance(got, Rejected) and "401" in str(got)


def test_apihub_typ01_is_decoded_as_euc_kr(monkeypatch: pytest.MonkeyPatch) -> None:
    """§6.8 ② — `help=1` 주석 헤더의 한글이 EUC-KR 이라 `r.text` 는 깨진다."""
    body = "# 지점번호 기온\n202608241735 108 30.9\n".encode("euc-kr")
    monkeypatch.setattr(kmahub, "request", lambda *a, **k: httpx.Response(200, content=body))
    monkeypatch.setattr(config, "KMA_HUB_KEY", "k" * 22)
    text = kmahub.get_text("/api/typ01/cgi-bin/url/nph-aws2_min", {"stn": 0})
    assert "지점번호" in text                       # 깨졌다면 여기서 걸린다
    assert httpx.Response(200, content=body).text != text


def test_apihub_rows_drops_comments_and_blanks() -> None:
    text = "#START7777\n# tm stn TA\n\n202608241735 108 30.9\n202608241736 108 30.8\n#7777END\n"
    assert kmahub.rows(text) == [["202608241735", "108", "30.9"], ["202608241736", "108", "30.8"]]


@pytest.mark.parametrize(("raw", "expected"), [
    ("30.9", 30.9), ("-99.9", None), ("-999", None), ("-50.0", None), ("-49.9", -49.9),
    ("", None), ("N/A", None),
])
def test_apihub_missing_values_become_none(raw: str, expected: float | None) -> None:
    """헤더 주석이 규정한 것은 `-99.9` 라는 값이 아니라 **-50 이하라는 구간**이다 (§6.8 ②)."""
    assert kmahub.value(raw) == expected


def test_apihub_operation_level_application_is_in_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "KMA_HUB_KEY", "")
    with pytest.raises(Rejected, match="오퍼레이션 단위"):
        kmahub.get_json("/api/typ02/openApi/LivingWthrIdxServiceV3/getSenTaIdxV3", {})


# ---------------------------------------------------------- 재시도 · 예산 (⑤-b)

@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "_sleep", lambda _seconds: None)


def _responder(monkeypatch: pytest.MonkeyPatch, responses: list[object]) -> list[int]:
    """호출 횟수를 세면서 준비된 응답을 차례로 낸다. 예외 객체면 던진다."""
    calls = [0]

    def fake_get(url, **kwargs):
        calls[0] += 1
        item = responses[min(calls[0] - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(base.httpx, "get", fake_get)
    return calls


def test_retryable_failure_is_retried_twice_then_given_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """⑤-b — 2회 재시도, 총 3시도."""
    calls = _responder(monkeypatch, [_envelope("05")])
    with pytest.raises(Unavailable):
        base.request("https://x/y", classify=datagokr._classify)
    assert calls[0] == base.MAX_RETRIES + 1 == 3


def test_retry_recovers_the_way_it_did_in_the_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    """실측은 `05` 가 1회 재시도로 2/2 복구됐다."""
    ok = _envelope("00")
    calls = _responder(monkeypatch, [_envelope("05"), ok])
    assert base.request("https://x/y", classify=datagokr._classify) is ok
    assert calls[0] == 2


def test_permanent_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """`30` 미신청은 몇 번을 해도 같다 — 예산만 태운다."""
    calls = _responder(monkeypatch, [_envelope("30")])
    with pytest.raises(Rejected):
        base.request("https://x/y", classify=datagokr._classify)
    assert calls[0] == 1


def test_network_errors_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """apihub 에서 반복 발생했고 재시도로 성공했다 (⑤-b)."""
    calls = _responder(monkeypatch, [httpx.ConnectTimeout("timed out")])
    with pytest.raises(Unavailable, match="ConnectTimeout"):
        base.request("https://x/y", classify=datagokr._classify)
    assert calls[0] == 3


def test_an_exhausted_budget_gives_up_without_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    """⑤-b — 오래 기다리느니 저하 응답을 준다. ⑤-a 가 그 답을 받아 준다."""
    calls = _responder(monkeypatch, [_envelope("00")])
    with pytest.raises(Unavailable, match="예산 초과"):
        base.request("https://x/y", classify=datagokr._classify, budget=Budget(seconds=-1))
    assert calls[0] == 0, "예산이 끝났는데도 호출했다"


def test_budget_default_comes_from_settings() -> None:
    assert Budget().total == config.REQUEST_BUDGET_SEC == 8.0
