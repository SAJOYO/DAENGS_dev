"""서울시 환경분야 누리집 파서 — 동물등록 안내·자진신고 기간.

정찰 (2026-08-27):
  - WordPress. 본문 `#view_ct`, 절 제목 `h4.cont-title-xl` / `h5.cont-title-lg`
  - 제목·수정일은 `#view_top` ("수정일 YYYY-MM-DD")
  - 지원 금액·기간이 해마다 바뀌므로 **`published_at` 이 특히 중요하다** — 답변에 그대로 실린다

`□`·`○` 로 시작하는 문단이 원문 그대로 남는다. 마커를 떼지 않는 이유는 공고문의 계층
표시라서다 — 떼면 "대상"과 "세부 조건"이 같은 층으로 보인다.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Heading
from ...extract import prose
from ..base import Parsed

NAME = "seoul_news_html"
VERSION = 1

CONTAINER = "#view_ct"
HEADINGS = ("h3", "h4", "h5")
_RE_MODIFIED = re.compile(r"수정일\s*(\d{4}-\d{2}-\d{2})")


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "lxml")

    title = doc.meta.get("document_title", "")
    published = doc.meta.get("published_at")
    top = soup.select_one("#view_top")
    if top is not None:
        head = top.get_text(" ", strip=True)
        if m := _RE_MODIFIED.search(head):
            published = m.group(1)
        if h := top.find(["h1", "h2", "h3"]):
            title = h.get_text(" ", strip=True) or title

    box = soup.select_one(CONTAINER)
    if box is None:
        raise RuntimeError(f"본문 컨테이너({CONTAINER})가 없다 — 페이지 구조가 바뀌었다")

    elements: list = []
    body = prose.elements(box, doc.doc_id, headings=HEADINGS)
    if not any(e.type == "heading" for e in body):
        elements.append(Heading(id=f"{doc.doc_id}#h2-0", level=2, text=title))
    elements += body

    return Parsed(elements=elements, document_title=title, published_at=published,
                  citation_url=doc.meta.get("source_url"))
