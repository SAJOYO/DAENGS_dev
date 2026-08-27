"""정부24 — 동물등록 민원안내 (AA020InfoCappView).

정찰 (2026-08-27):
  - **시드에 있던 `portal/service/serviceInfo/PTR000051610` 은 죽었다.** 200 을 주면서
    본문이 '서비스를 찾을 수 없습니다' 인 soft-404 다 (브라우저 UA 로도 같다).
    살아 있는 것은 `/mw/AA020InfoCappView.do?CappBizCD=...` 쪽이다.
  - **robots.txt 가 `Disallow: /` 를 먼저 적고 그 아래에서 이 경로만 열어 준다.**
    표준(RFC 9309 §2.2.2)의 longest-match 로 읽어야 허용으로 판정된다 — `core/fetch.py` 참고.
  - 인코딩 **EUC-KR**. 본문 일부가 숫자 HTML 엔티티(`&#51064;`)로 들어온다 (bs4 가 풀어 준다)
  - 제목 `#pageCont h2`, 본문 `.contentsWrap .wrap_col`
  - 접속량이 많으면 같은 URL 이 **대기열 페이지**를 준다. 그때는 `.wrap_col` 이 없다 →
    본문 없음으로 처리해 sha256 지문이 대기열 문구로 덮이지 않게 한다
  - 발행일에 해당하는 표시가 없다 (민원 안내는 상시). published_at 은 비운다
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from ...core import textutil
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

BASE = "https://www.gov.kr/mw/AA020InfoCappView.do"

# 민원 코드는 사이트 개편과 무관하게 유지되는 식별자라 여기(층 ③)에 둔다.
CAPPS = [
    ("15410000003", "apply", "동물등록 신청·변경신고"),
    ("15410000007", "reissue", "동물등록증 재발급 신청"),
]


class Gov24Registration(Source):
    id = "gov24-registration"
    domain = "registration"
    category = "policy"
    subcategory = "registration"
    source_type = "web"
    format = "html"
    trust_level = "official"            # 민원 안내. 근거 조문은 cites 로 보존
    license = "공공누리 제1유형"

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        return [
            Target(url=f"{BASE}?CappBizCD={code}", slug=f"{self.id}-{name}",
                   ext="html", meta={"title": title})
            for code, name, title in CAPPS
        ]

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "lxml")           # meta charset=euc-kr 을 bs4 가 읽는다

        h2 = soup.select_one("#pageCont h2")
        title = h2.get_text(" ", strip=True) if h2 else target.meta.get("title", "")

        box = soup.select_one(".contentsWrap .wrap_col")
        if box is None:
            # 대기열 페이지이거나 구조가 바뀐 것. 대기열 문구를 본문으로 저장하면
            # 그것이 지문이 되어 다음 크롤이 '변경됨'으로 뜬다.
            return Extracted(title=title, text="",
                             extra={"warning": "본문 컨테이너 없음 (접속 대기열?)"})

        for t in box.select("script, style"):
            t.decompose()
        text = textutil.squeeze(textutil.block_text(box))
        return Extracted(title=title, text=text, cites=textutil.cites(text))
