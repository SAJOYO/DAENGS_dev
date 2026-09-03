"""서울시 환경분야 누리집 — 동물등록 안내·자진신고 기간.

지자체 지원 도메인(§6)의 ③신선도 계층에 해당하는 **대표 사례**다. 전국 전수는 보조금24·조례
API 카드가 맡고, 여기서는 키 없이 되는 서울 한 곳만 받는다.

정찰 (2026-08-27):
  - WordPress. 본문 `#view_ct`, 제목·수정일 `#view_top` ("수정일 YYYY-MM-DD")
  - robots 가 `/files` 와 `?s=` 검색을 막는다 → 첨부파일은 받지 않는다
  - **시드에 있던 522690 은 2023년 글이라 쓰지 않는다.** 지원 금액·마릿수가 해마다 바뀌는데
    낡은 값이 코퍼스에 들어가면 `/life/ask` 가 자신 있게 틀린 금액을 답한다. 2026년 글로 바꿨다
  - `published_at` 에 수정일이 들어가므로, 답변에 실리는 날짜로 사용자가 낡음을 알아챌 수 있다
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ...core import textutil
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

BASE = "https://news.seoul.go.kr/env/archives"

# archives 번호는 글 고유번호다. 해가 바뀌면 새 글이 생기므로 **연 1회 사람이 확인해야 한다**
# (seed notes 에도 같은 말이 적혀 있다).
ARTICLES = [
    ("544026", "registration-guide", "동물등록으로 반려동물을 지켜주세요! (등록 방법·수수료·과태료)"),
    ("569082", "voluntary-report", "2026년 동물등록 자진신고기간 운영"),
]


class SeoulMicrochipSupport(Source):
    id = "seoul-microchip-support"
    domain = "subsidy"
    category = "policy"
    subcategory = "subsidy"
    source_type = "web"
    format = "html"
    trust_level = "official"
    license = "공공누리 제1유형"

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        return [
            Target(url=f"{BASE}/{archive_id}", slug=f"{self.id}-{name}",
                   ext="html", meta={"title": title})
            for archive_id, name, title in ARTICLES
        ]

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "lxml")

        title = target.meta.get("title", "")
        published = None
        top = soup.select_one("#view_top")
        if top is not None:
            head = top.get_text(" ", strip=True)
            if m := re.search(r"수정일\s*(\d{4}-\d{2}-\d{2})", head):
                published = m.group(1)
            if h := top.find(["h1", "h2", "h3"]):
                title = h.get_text(" ", strip=True) or title

        box = soup.select_one("#view_ct")
        if box is None:
            return Extracted(title=title, text="", published_at=published,
                             extra={"warning": "본문 컨테이너 없음"})

        for t in box.select("script, style"):
            t.decompose()
        text = textutil.squeeze(textutil.block_text(box))
        return Extracted(title=title, text=text, published_at=published, cites=textutil.cites(text))
