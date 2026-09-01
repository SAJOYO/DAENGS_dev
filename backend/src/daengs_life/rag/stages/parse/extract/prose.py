"""포맷 층 ① — HTML 해설 문서를 heading / para 로 편다 (RAG-018).

법령은 조문이 경계를 확정해 주지만(`article`), **기관 해설 페이지는 그런 것이 없다.**
대신 어느 사이트나 `제목 태그 + 그 아래 블록들` 이라는 같은 모양을 쓴다 — 정부24는
`h3.tit_dep_2`, 국립축산과학원은 `h3.subtit`, 서울시는 `h5.cont-title-lg` 다.
**태그 이름만 다르고 구조가 같아서** 사이트마다 같은 코드를 쓰게 되므로 여기(포맷 층)로 올린다.
사이트별로 다른 것(어느 컨테이너, 어느 제목 태그, 무엇이 노이즈인가)은 인자로 받는다.

**가장 안쪽 블록만 문단으로 삼는다.** `ul > li > p` 를 전부 뽑으면 같은 문장이 세 번 들어간다.
자기 안에 다른 블록이 없는 것만 남기면 중복 없이 원문 순서가 보존된다.
"""
from __future__ import annotations

from typing import Any

from daengs_life.crawler.core import textutil
from daengs_life.rag.core.ir import AnyElement, Heading, Para

# 문단이 될 수 있는 태그. div 가 들어 있는 이유는 정책브리핑처럼 <p> 없이 <div> + <br> 로만
# 쓰는 페이지가 있어서다 — '가장 안쪽' 규칙이 래퍼 div 를 알아서 걸러 준다.
BLOCK = ("p", "li", "dd", "dt", "td", "div", "blockquote")
NOISE = "script, style, input, label, button, .sliderkit"


def elements(box: Any, doc_id: str, *, headings: tuple[str, ...] = ("h3", "h4", "h5"),
             noise: str = NOISE, start: int = 0) -> list[AnyElement]:
    """컨테이너 하나 → [Heading(level=2) | Para] 목록. 원문 순서 그대로.

    `start` 는 이미 만든 요소 수 — 파서가 문서 제목 heading 을 먼저 얹는 경우 번호가 겹치지 않게 한다.
    """
    for tag in box.select(noise):
        tag.decompose()

    inner = tuple(headings) + BLOCK
    out: list[AnyElement] = []
    n = {"h": 0, "p": start}

    for node in box.find_all(inner):
        if node.name in headings:
            text = _clean(node)
            if not text:
                continue
            n["h"] += 1
            out.append(Heading(id=f"{doc_id}#h2-{n['h']}", level=2, text=text))
            continue
        if node.find(inner) is not None:          # 안에 또 블록이 있으면 래퍼다 — 안쪽에서 잡는다
            continue
        text = _clean(node)
        if not text:
            continue
        n["p"] += 1
        out.append(Para(id=f"{doc_id}#p-{n['p']}", text=text, level=3,
                        cites=textutil.cites(text)))
    return out


def _clean(node: Any) -> str:
    """블록 경계만 줄바꿈. 조항 인용이 `<a>` 로 끊기지 않게 인라인은 공백으로 잇는다."""
    return textutil.squeeze(textutil.block_text(node))
