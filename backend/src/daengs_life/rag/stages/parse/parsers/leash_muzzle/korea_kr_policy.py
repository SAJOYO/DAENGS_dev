"""정책브리핑 기사 파서 — 맹견 안전관리 제도 해설.

정찰 (2026-08-27):
  - 본문 `.view_cont` (`.article_body` 는 이미지 갤러리까지 포함한다)
  - 제목 `.article_head h2`. **카드뉴스형 기사에는 h2 가 없어** `<title>` 에서 접미사를 뗀다
  - 발행일 `.article_head .info span` 첫 번째 (`YYYY.MM.DD`)
  - **절 제목이 없다.** 기사라 소제목 없이 문단만 이어진다 → 문서 제목을 절 제목으로 세운다
  - `.sliderkit` 는 같은 캡션이 슬라이드 수만큼 반복된다 (`prose.NOISE` 가 제거한다)

법령 조문은 `law-drf-api` 가 갖고 있고 여기는 그 위의 시행 안내다. 기사 본문은 「」 없이
법령을 부르는 일이 많아 `cites` 가 비는 것이 정상이다.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Heading
from ...extract import prose
from ..base import Parsed

NAME = "korea_kr_html"
VERSION = 1

CONTAINER = ".view_cont"
_RE_DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "lxml")

    h2 = soup.select_one(".article_head h2")
    if h2 is not None:
        title = h2.get_text(" ", strip=True)
    else:
        raw_title = soup.title.get_text(" ", strip=True) if soup.title else ""
        title = raw_title.split(" - ")[0].strip() or doc.meta.get("document_title", "")

    published = doc.meta.get("published_at")
    info = soup.select_one(".article_head .info span")
    if info and (m := _RE_DATE.search(info.get_text(strip=True))):
        published = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    box = soup.select_one(CONTAINER) or soup.select_one(".article_body")
    if box is None:
        raise RuntimeError(f"본문 컨테이너({CONTAINER})가 없다 — 페이지 구조가 바뀌었다")

    # 기사에는 소제목이 없다. 절 제목이 하나도 없으면 청커가 문단을 담을 그릇이 없으므로
    # 문서 제목으로 절을 하나 세운다 (캡션에서 중복은 청커가 지운다).
    elements: list = [Heading(id=f"{doc.doc_id}#h2-0", level=2, text=title)]
    elements += prose.elements(box, doc.doc_id, headings=())

    return Parsed(elements=elements, document_title=title, published_at=published,
                  citation_url=doc.meta.get("source_url"))
