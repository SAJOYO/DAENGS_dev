"""국립축산과학원 반려동물 포털 파서 — 행정/법률 해설.

정찰 (2026-08-27):
  - 정적 HTML, UTF-8. 본문 `section#contents`, 문서 제목 `h2.pageTitle`, 절 제목 `h3.subtit`
  - 절 안은 `ul.inDiv1 > li` 이고 `<strong>` 가 항목 이름이다 (인라인이라 문단에 그대로 남는다)
  - `.tabscontents` 는 탭 UI 잔여물이라 뺀다
  - 발행일 표시가 없다

**조문 인용이 본문에 그대로 있다** (「동물보호법」 제15조 1항). `textutil.cites` 가 문단마다
뽑아 주므로 해설 청크가 어느 조문에 근거하는지 남는다 — easylaw 와 같은 성격이다.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Heading
from ...extract import prose
from ..base import Parsed

NAME = "nias_html"
VERSION = 1

CONTAINER = "section#contents"
HEADINGS = ("h3",)


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "lxml")

    box = soup.select_one(CONTAINER)
    if box is None:
        raise RuntimeError(f"본문 컨테이너({CONTAINER})가 없다 — 페이지 구조가 바뀌었다")

    h2 = box.select_one("h2.pageTitle")
    title = (h2.get_text(" ", strip=True) if h2 else "") or doc.meta.get("document_title", "")
    if h2 is not None:
        h2.decompose()                      # 문서 제목은 헤더로 가므로 문단으로 또 넣지 않는다

    elements: list = []
    body = prose.elements(box, doc.doc_id, headings=HEADINGS,
                          noise=prose.NOISE + ", .tabscontents")
    if not any(e.type == "heading" for e in body):
        elements.append(Heading(id=f"{doc.doc_id}#h2-0", level=2, text=title))
    elements += body

    return Parsed(elements=elements, document_title=title,
                  citation_url=doc.meta.get("source_url"))
