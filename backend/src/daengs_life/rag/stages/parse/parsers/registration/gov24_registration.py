"""정부24 민원안내(AA020InfoCappView) 파서 — 서비스 개요·수수료·구비서류.

정찰 (2026-08-27):
  - 인코딩 EUC-KR. `BeautifulSoup(raw, "lxml")` 이 meta charset 을 읽어 준다
  - 본문 `.contentsWrap .wrap_col`, 절 제목 `h3.tit_dep_2` / 세부 `h4.tit_dep_3`
  - 항목은 `p.tit`(이름) + `p.txt`(값) 쌍이라 문단 하나가 곧 `신청방법 인터넷, 방문` 이 된다
  - **접속 대기열 페이지가 오면 `.wrap_col` 이 없다.** 그때는 빈 문서로 두지 않고 실패시킨다 —
    조용히 0요소로 지나가면 재파싱해도 계속 0 이다
  - 발행일 표시가 없다 (민원 안내는 상시)

수수료·처리기간이 이 소스의 값이다. 조문에는 금액이 없고 시행규칙 별표에만 있는데,
여기는 `내장형 10,000원 / 외장형 3,000원` 이 한 문단에 들어 있다.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Heading
from ...extract import prose
from ..base import Parsed

NAME = "gov24_html"
VERSION = 1

CONTAINER = ".contentsWrap .wrap_col"
HEADINGS = ("h3", "h4")


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "lxml")

    box = soup.select_one(CONTAINER)
    if box is None:
        raise RuntimeError(f"본문 컨테이너({CONTAINER})가 없다 — 접속 대기열이거나 구조가 바뀌었다")

    h2 = soup.select_one("#pageCont h2")
    title = (h2.get_text(" ", strip=True) if h2 else "") or doc.meta.get("document_title", "")

    elements: list = []
    body = prose.elements(box, doc.doc_id, headings=HEADINGS)
    if not any(e.type == "heading" for e in body):
        elements.append(Heading(id=f"{doc.doc_id}#h2-0", level=2, text=title))
    elements += body

    return Parsed(elements=elements, document_title=title,
                  citation_url=doc.meta.get("source_url"))
