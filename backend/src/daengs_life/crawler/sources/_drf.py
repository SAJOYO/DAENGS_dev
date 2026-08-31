"""법제처 DRF Open API 의 **호출 규약**. 파싱은 각 소스가 한다.

`law-drf-api`(법령)와 `ordinance-search`(자치법규)가 같은 문 앞에 선다 — 같은 호스트, 같은
`OC` 인증, 같은 "실패도 HTTP 200 으로 준다"는 성질. 여기 있는 것은 그 문에 관한 것뿐이다.

**본문 규격은 공유하지 않는다.** 2026-08-27 정찰에서 확인한 대로 두 target 의 XML 은 태그가
겹치는 것이 하나도 없다:

    target=law    <법령>   <기본정보>       <조문단위>…<항><호><목>  <부칙단위> <별표단위>
    target=ordin  <LawService> <자치법규기본정보> <조문><조>            <부칙>

그래서 `extract()` 를 공통 베이스로 올리면 두 규격의 분기가 베이스 안에 쌓인다. 올리는 것은
**어느 target 이든 똑같은 것** — 인증 실패 판정, 날짜 정규화, 응답 미리보기 — 세 가지다.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

SEARCH = "http://www.law.go.kr/DRF/lawSearch.do"
SERVICE = "http://www.law.go.kr/DRF/lawService.do"

OC_MISSING = (
    "LAW_OC 미설정. open.law.go.kr 에서 OPEN API 를 신청하면 즉시 발급된다(무료).\n"
    "  발급 후 레포 루트 .env 에 `LAW_OC=발급받은_이메일ID` 한 줄을 추가하면 된다.\n"
    "  (docs/life/data-sources.md §9)"
)


def preview(content: bytes, n: int = 400) -> str:
    return content[:n].decode("utf-8", "replace").replace("\n", " ").strip()


def text(node, *names: str) -> str | None:
    """자손 중 이름이 names 안에 있는 첫 태그의 텍스트. 규격 변형을 흡수한다."""
    for n in names:
        el = node.find(n)
        if el is not None and el.get_text(strip=True):
            return el.get_text(strip=True)
    return None


def ymd(raw: str | None) -> str | None:
    """'20260707' → '2026-07-07'. 이미 구분자가 있거나 형식이 다르면 그대로 돌려준다."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) == 8 else raw


def check_auth_error(content: bytes) -> None:
    """DRF 는 인증 실패도 HTTP 200 + XML 로 준다. 규격 오류로 오진하지 않도록 먼저 걸러낸다.

    실제 응답 (2026-08-20, 잘못된 OC 로 확인):
      <Response><result>사용자 정보 검증에 실패하였습니다.</result>
                <msg>OPEN API 호출 시 사용자 검증을 위하여 정확한 서버장비의
                     IP주소 및 도메인주소를 등록해 주세요.</msg></Response>
    """
    soup = BeautifulSoup(content, "xml")
    if soup.find("Response") is None:
        return
    raise RuntimeError(
        f"법령 API 인증 실패: {text(soup, 'result') or ''}\n"
        f"  {text(soup, 'msg') or ''}\n"
        "  OC 값이 맞는지, open.law.go.kr 에서 이 PC 의 IP/도메인을 등록했는지 확인할 것."
    )


def check_result_code(soup: BeautifulSoup, where: str) -> None:
    """목록조회 성공 응답은 <resultCode>00</resultCode><resultMsg>success</resultMsg>."""
    if (code := text(soup, "resultCode")) and code != "00":
        raise RuntimeError(f"{where} 실패 code={code} msg={text(soup, 'resultMsg')}")
