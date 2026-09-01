"""SRT 반려동물 동반탑승 안내 파서 — 본문이 이미지 `alt` 하나다 (RAG-036 ②).

**페이지에 텍스트가 없다.** `.sub_con_area` 의 실제 텍스트는 제목 17자뿐이고, 이동장 규격·무게·
예방접종 요건이 전부 `<img alt>` 안에 들어 있다. 크롤러 소스(`crawler/sources/transport/srt_terms.py`)
가 `Extracted.text` 를 만들 때 이미 그 `alt` 를 합쳐 두었지만, **파서는 원본 HTML 을 다시 읽으므로
여기서도 같은 판단을 해야 한다** — 안 하면 파싱 결과가 제목만 남은 빈 문서가 된다.

`alt` 한 덩어리를 그대로 한 문단으로 두지 않고 `-탑승가능한 반려동물 :` `-준수사항 :` 마커에서
자른다. 원문이 그 두 축으로 쓰여 있고, 사용자의 질문("이동장 크기" / "꺼내도 되나")도 그 축으로
갈리기 때문이다. 문서가 500자대라 청커는 어차피 소제목 하나 밑에 다 모아 한 청크로 만든다.

**원문 오기는 고치지 않는다** — `시각,청각,지체장애인 보저견`(보조견). 코퍼스는 원문 재현이
원칙이고, 대신 렉시컬 축이 "보조견" 으로는 이 문장을 못 집는다는 것을 알고 있어야 한다.
`(PDF다운로드)` 는 이미지가 겸하는 다운로드 버튼의 라벨이라 뺀다 — 내용이 아니라 UI 다.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from daengs_life.crawler.core import textutil
from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Heading, Para
from .seoulmetro_terms import with_org
from ..base import Parsed

NAME = "srt_html"
VERSION = 1

CONTAINER = ".sub_con_area"

# `… 알려드립니다.-탑승가능한 반려동물 : …  -준수사항 : …` — 하이픈 뒤에 `짧은 이름 :` 이 오는
# 자리만 문단 경계로 본다. 본문의 `45X30X25cm` 같은 하이픈 없는 수치는 건드리지 않는다.
_RE_SECTION = re.compile(r"\s*-\s*(?=[가-힣][가-힣 ]{1,12}\s*:)")
_RE_UI_NOISE = re.compile(r"\s*\(PDF다운로드\)\s*\.*")


def segments(alt: str) -> list[str]:
    """이미지 `alt` 한 덩어리 → 문단 목록."""
    cleaned = _RE_UI_NOISE.sub(" ", alt)
    return [textutil.squeeze(part).strip() for part in _RE_SECTION.split(cleaned) if part.strip()]


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "lxml")

    box = soup.select_one(CONTAINER)
    if box is None:
        raise RuntimeError(f"본문 컨테이너({CONTAINER})가 없다 — 페이지 구조가 바뀌었다")
    for tag in box.select("script, style"):
        tag.decompose()

    h = box.find(["h2", "h3", "h4"])
    title = with_org((h.get_text(" ", strip=True) if h else "")
                     or doc.meta.get("document_title", ""), doc.meta.get("source", ""))

    doc_id = doc.doc_id
    elements: list = [Heading(id=f"{doc_id}#h2-0", level=2, text=title)]
    warnings: list[str] = []

    n = 0
    for img in box.find_all("img"):
        for part in segments(img.get("alt") or ""):
            n += 1
            elements.append(Para(id=f"{doc_id}#p-{n}", text=part, level=3,
                                 cites=textutil.cites(part)))
    if not n:
        # 이미지가 텍스트로 바뀌었거나(좋음) 교체된 것(나쁨). 어느 쪽이든 사람이 봐야 한다
        warnings.append("이미지 alt 가 없다 — 본문을 통째로 잃었을 수 있다")
        for p in box.find_all(["p", "li"]):
            text = textutil.squeeze(textutil.block_text(p))
            if not text:
                continue
            n += 1
            elements.append(Para(id=f"{doc_id}#p-{n}", text=text, level=3,
                                 cites=textutil.cites(text)))

    return Parsed(elements=elements, document_title=title,
                  published_at=doc.meta.get("published_at"),
                  citation_url=doc.meta.get("source_url"),
                  counts={"문단": n}, warnings=warnings)
