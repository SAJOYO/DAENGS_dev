"""정책브리핑(korea.kr) — 맹견 안전관리 제도 해설.

법령 본문은 `law-drf-api`(조문 단위 XML)가 갖고 있다. 여기는 그 위의 **해설·시행 안내 계층**이다
(docs/data-sources.md §4). 조문만으로는 "언제부터", "계도기간이 있나" 같은 질문에 답할 수 없다.

정찰 (2026-08-27):
  - 정적 HTML, UTF-8. robots 는 전면 허용 (검색 경로만 Googlebot 에 제한)
  - 제목 `.article_head h2`, 발행일 `.article_head .info span`(첫 번째, `YYYY.MM.DD`)
  - 본문 `.view_cont` (`.article_body` 는 이미지 갤러리까지 포함한다)
  - **카드뉴스형 기사는 `.article_head h2` 가 없다.** 그때는 `<title>` 에서 접미사를 떼어 쓴다
  - 카드뉴스의 `.sliderkit` 는 같은 캡션이 슬라이드 수만큼 반복된다 → 제거하지 않으면
    본문 지문(sha256)이 캡션 반복으로 채워진다
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ...core import textutil
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

BASE = "https://www.korea.kr/news/policyNewsView.do"

# newsId 는 기사 고유번호라 사이트 개편과 무관하다. 맹견사육허가제의 세 국면을 고른다 —
# 도입(법 통과) · 시행 상세 · 계도기간. 하나만 받으면 "지금 어떻게 되어 있나"에 답이 안 된다.
ARTICLES = [
    ("148900501", "act-passed", "동물보호법 전부 개정안 국회 통과 (맹견사육허가제 도입)"),
    ("148926123", "permit-start", "4월부터 맹견 키우려면 허가 필수 — 달라지는 맹견 안전관리 제도"),
    ("148935517", "grace-period", "맹견사육허가제 계도기간 운영"),
]

_RE_DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")


class KoreaKrPolicy(Source):
    id = "korea-kr-policy"
    domain = "leash-muzzle"
    category = "policy"
    subcategory = "leash-muzzle"
    source_type = "web"
    format = "html"
    trust_level = "official"            # 정부 발표 해설. 근거 조문 자체는 law-drf-api 가 갖는다
    license = "공공누리 제1유형"

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        return [
            Target(url=f"{BASE}?newsId={news_id}", slug=f"{self.id}-{name}",
                   ext="html", meta={"title": title})
            for news_id, name, title in ARTICLES
        ]

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "lxml")

        h2 = soup.select_one(".article_head h2")
        if h2 is not None:
            title = h2.get_text(" ", strip=True)
        else:                                              # 카드뉴스형
            raw = soup.title.get_text(" ", strip=True) if soup.title else ""
            title = raw.split(" - ")[0].strip() or target.meta.get("title", "")

        published = None
        info = soup.select_one(".article_head .info span")
        if info and (m := _RE_DATE.search(info.get_text(strip=True))):
            published = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        box = soup.select_one(".view_cont") or soup.select_one(".article_body")
        if box is None:
            return Extracted(title=title, text="", published_at=published,
                             extra={"warning": "본문 컨테이너 없음"})

        for t in box.select("script, style, .sliderkit"):
            t.decompose()
        text = textutil.squeeze(textutil.block_text(box))
        return Extracted(title=title, text=text, published_at=published, cites=textutil.cites(text))
