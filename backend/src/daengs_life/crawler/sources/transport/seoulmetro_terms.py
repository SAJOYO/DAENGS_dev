"""서울교통공사 여객운송약관 — 1~8호선 + 9호선 2·3단계.

`transport` 도메인의 첫 소스다. 「동물보호법」이 답하지 못하는 축 — **운송사업자가 자기 약관에서
동물을 어떻게 다루는가** — 을 원문으로 갖는다. 휴대금지품 조항의 단서가 그것이다:
*"동물. 다만, 소수량의 조류, 소충류 및 크기가 작은 애완동물로서 전용 이동장 등에 넣어 보이지 않게
하고, 불쾌한 냄새가 발생하지 않도록 한 경우와 … 장애인보조견은 제외합니다."*

정찰 (2026-08-28, #40 시드 + 이 카드에서 실측):
  - **HTTPS 가 열리지 않는다. http 로만 받는다** — 인증서가 아니라 443 자체가 응답하지 않는다
  - **PDF 가 아니라 HTML 본문이다.** 시드의 `pdf-entry` 분류가 틀렸다 (RAG-036 ①)
  - 본문 `#contents`. 장은 `h4.title1`, 조는 `h5`, 항은 `ol.list1 > li`(`①` 이 텍스트에 들어 있다)
  - **1~8호선(`menuIdx=528`)과 9호선 2·3단계(`menuIdx=779`)는 약관이 다르다.** 운영 주체가 같아도
    9호선 2·3단계는 별도 규정이라 개정 이력·조 번호가 어긋난다 → 문서 둘로 받는다
  - 개정 이력이 본문 맨 위에 `제정 2017. 5. 31. 규정 제60호 / 개정 2026. 3. 7. 규정 제577호` 로 있다.
    **마지막 개정일을 `published_at` 으로 쓴다** — 약관은 개정 시행일이 곧 그 문서의 유효 시점이다
  - 페이지에 공공누리 표기가 없어 `license` 를 비운다. 공공기관 규정이지만 표기가 없으면 적지 않는다
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ...core import textutil
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

BASE = "http://www.seoulmetro.co.kr/kr/page.do"
CONTAINER = "#contents"

# menuIdx 는 메뉴 고유번호다. 사이트 개편 때 바뀔 수 있어 seed notes 에도 같이 적어 둔다.
DOCUMENTS = [
    ("528", "line1-8", "서울교통공사 여객운송약관(1~8호선)"),
    ("779", "line9", "서울교통공사 여객운송약관(9호선 2·3단계)"),
]

# `제정 2017. 5. 31.` `개정 2026. 3. 7.` — 공백이 들쭉날쭉하다.
_RE_REVISION = re.compile(r"(제정|개정)\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.")
# 개정 이력은 **제1장 앞**에만 있다. 뒤까지 훑으면 부칙의 `부 칙(2026.03.07.)` 가 섞여 들어오는데,
# 부칙 날짜가 더 나중인 경우가 있어 published_at 이 조용히 시행 예정일로 밀린다.
_RE_FIRST_CHAPTER = re.compile(r"제\s*1\s*장")


def revision_date(text: str) -> str | None:
    """본문 머리의 제정·개정 이력 중 **가장 나중 날짜**. 이력이 없으면 None."""
    head = text[: m.start()] if (m := _RE_FIRST_CHAPTER.search(text[:2000])) else text[:1200]
    found = sorted(
        f"{m.group(2)}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
        for m in _RE_REVISION.finditer(head)
    )
    return found[-1] if found else None


class SeoulmetroTerms(Source):
    id = "seoulmetro-terms"
    domain = "transport"
    category = "travel"                 # 동반 이동은 policy 가 아니다 (data/README 값 사전)
    subcategory = "transport-rail"
    source_type = "web"                 # 약관이지만 파일이 아니라 HTML 본문이다
    format = "html"
    trust_level = "official"            # 사업자 약관 — 법률이 아니므로 law 가 아니다
    license = ""                        # 페이지에 공공누리 표기 없음

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        return [
            Target(url=f"{BASE}?menuIdx={menu_idx}", slug=f"{self.id}-{name}",
                   ext="html", meta={"title": title})
            for menu_idx, name, title in DOCUMENTS
        ]

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "lxml")

        # `<title>` 은 `여객운송약관(1~8호선) : 이용정보>운임제도>…` 다 — 앞부분만 쓴다.
        raw_title = soup.title.get_text(" ", strip=True) if soup.title else ""
        title = raw_title.split(":")[0].strip() or target.meta.get("title", "")

        box = soup.select_one(CONTAINER)
        if box is None:
            return Extracted(title=title, text="",
                             extra={"warning": f"본문 컨테이너({CONTAINER}) 없음"})

        for t in box.select("script, style"):
            t.decompose()
        text = textutil.squeeze(textutil.block_text(box))
        return Extracted(title=title, text=text, published_at=revision_date(text),
                         cites=textutil.cites(text))
