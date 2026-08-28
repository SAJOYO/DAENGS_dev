"""서울교통공사 여객운송약관 파서 — 조문형 HTML (RAG-036).

**해설이 아니라 약관이다.** 시드가 `pdf-entry` 로 분류했던 문서인데 실제로는 HTML 본문이고,
게다가 문단이 아니라 **조문이 경계를 확정해 준다** — 장(`h4`) / 조(`h5`) / 항(`ol.list1 > li`,
`①` 이 텍스트에 들어 있다) / 호(중첩 `ol > li`, `1.`). 그래서 카드가 예상한 `prose.py` 가 아니라
`Article` 을 만든다. 법령 파서(`law_drf_api`)와 같은 IR 을 쓰므로 청커는 아무것도 안 바뀐다.

이 문서가 코퍼스에 있는 이유는 제35조(휴대금지품) 제1항제4호다 —
*"동물. 다만, … 크기가 작은 애완동물로서 전용 이동장 등에 넣어 보이지 않게 하고, 불쾌한 냄새가
발생하지 않도록 한 경우와 … 장애인보조견은 제외합니다."* 조 단위 청크라 단서가 본문과 붙어 있다.

구조 (2026-08-28 실측, 1~8호선 · 9호선 2·3단계 공통)
  - 본문 `#contents`, 마디는 `div.terms-area`. **문서 순서대로 한 번 훑는다** — 별표 3 처럼
    표가 `ul.diamond-txt` 안에 들어앉은 경우가 있어 직계 자식만 보면 놓친다
  - `h5` 는 조 제목과 별표 제목 **둘 다** 쓴다. `제N조` 정규식으로 가른다 (별표 쪽은 `.ag-c`)
  - `[별표 N]` 은 `<p>` 로 표 앞에 따로 있다. 표의 `<caption>` 에는 번호가 없어 둘을 합쳐야
    `별표 3` 이라는 인용 문자열이 나온다
  - 부칙은 `h4` + `p` 쌍이고 전부 시행일 단문이다 → 청커가 `부칙: 단문 시행일` 로 버린다(의도됨).
    **제목의 공백(`부 칙(2026.03.07.)`)을 정규화하지 않는다** — 정규화하면 청커의 타법개정
    판정에 잘못 걸려 드롭 사유가 바뀐다
  - 별표에 딸린 단서 문단("다만, 청소년이 1회권을 이용할 경우에는 …")은 `Article(paragraphs=[])`
    로 낸다. `Para` 로 내면 조문형 문서에서 청커가 조용히 버린다 (RAG-034 ④ 가 같은 자리에서
    내린 결론이다)
"""
from __future__ import annotations

import copy
import re

from bs4 import BeautifulSoup

from daengs_life.crawler.core import textutil
from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Article, Heading, Item, Para, Paragraph, SubItem, Table
from ...extract import htmltable
from ..base import Parsed

NAME = "seoulmetro_html"
VERSION = 1

CONTAINER = "#contents"

_CIRCLED = {chr(0x2460 + i): i + 1 for i in range(20)}          # ① … ⑳
_RE_ARTICLE = re.compile(r"^제\s*(\d+)\s*조(?:의\s*(\d+))?")
_RE_CHAPTER = re.compile(r"^제\s*(\d+)\s*장")
_RE_ATTACH = re.compile(r"^\[\s*별표\s*(\d+)\s*\]")
_RE_RELATED = re.compile(r"[(（]([^)）]*관련)[)）]")
_RE_ITEM = re.compile(r"^\s*(\d+)\s*\.")
_RE_SUBITEM = re.compile(r"^\s*([가-힣])\s*\.")
_RE_SUPPLEMENTARY = re.compile(r"^부\s*칙")
_RE_REVISION = re.compile(r"(제정|개정)\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.")


def _text(node) -> str:
    return textutil.squeeze(textutil.block_text(node))


def with_org(title: str, org: str) -> str:
    """**운영기관을 제목 앞에 붙인다** (RAG-034 ⑥ 의 처방을 그대로 쓴다).

    페이지의 `<title>` 은 `여객운송약관(1~8호선)` 이라 **어디 약관인지가 본문 어디에도 없다.**
    청크 본문이 `{document_title} {head}` 로 조립되므로(`chunk._article`) 제목에 넣는 것만으로
    기관명이 임베딩과 렉시컬 양쪽에 들어간다. 이미 들어 있으면 겹쳐 쓰지 않는다.
    """
    if not org or org in title:
        return title
    return f"{org} {title}".strip()


def _own_text(li) -> str:
    """`<li>` 자기 텍스트 — 중첩 `<ol>`(호·목)은 뺀다. 안 빼면 항 두문에 호가 통째로 겹친다."""
    clone = copy.copy(li)
    for sub in clone.find_all("ol"):
        sub.decompose()
    return _text(clone)


def _subitems(li) -> list[SubItem]:
    """목 — `가. 나. 다.`. 호 밑에 `<ol>` 이 한 겹 더 있을 때만 생긴다 (문서당 6건)."""
    return [
        SubItem(no=(m.group(1) if (m := _RE_SUBITEM.match(t)) else ""), text=t)
        for deeper in li.find_all("ol", recursive=False)
        for t in (_own_text(x) for x in deeper.find_all("li", recursive=False))
        if t
    ]


def _items(sub_ol) -> list[Item]:
    """호 — `1. 2. 3.`."""
    items: list[Item] = []
    for li in sub_ol.find_all("li", recursive=False):
        text = _own_text(li)
        items.append(Item(no=(m.group(1) if (m := _RE_ITEM.match(text)) else ""),
                          text=text, subitems=_subitems(li)))
    return items


def _paragraphs(ol) -> list[Paragraph]:
    """항 — `① ② ③`. 번호가 없으면 sym 없이 두는데, 호로 쓸지는 호출부가 정한다."""
    out: list[Paragraph] = []
    for li in ol.find_all("li", recursive=False):
        text = _own_text(li)
        sym = text[0] if text and text[0] in _CIRCLED else None
        items: list[Item] = []
        for sub in li.find_all("ol", recursive=False):
            items += _items(sub)
        out.append(Paragraph(no=_CIRCLED.get(sym or ""), sym=sym, text=text, items=items))
    return out


def _chars(head: str, paras: list[Paragraph]) -> int:
    n = len(head)
    for p in paras:
        n += len(p.text) + sum(len(i.text) + sum(len(s.text) for s in i.subitems) for i in p.items)
    return n


def revision_date(text: str) -> str | None:
    """머리말의 제정·개정 이력 중 가장 나중 날짜. 부칙 날짜가 섞이지 않게 제1장 앞만 본다."""
    head = text[: m.start()] if (m := re.search(r"제\s*1\s*장", text[:2000])) else text[:1200]
    found = sorted(f"{m.group(2)}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
                   for m in _RE_REVISION.finditer(head))
    return found[-1] if found else None


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "lxml")
    doc_id = doc.doc_id

    raw_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title = with_org(raw_title.split(":")[0].strip() or doc.meta.get("document_title", ""),
                     doc.meta.get("source", ""))

    box = soup.select_one(CONTAINER)
    if box is None:
        raise RuntimeError(f"본문 컨테이너({CONTAINER})가 없다 — 페이지 구조가 바뀌었다")
    for tag in box.select("script, style"):
        tag.decompose()

    elements: list = []
    counts = {"조": 0, "장": 0, "별표": 0, "부칙": 0, "머리말 문단": 0}

    article: dict | None = None                 # 조립 중인 조
    supplementary: str | None = None            # 부칙 제목
    attach_section: str | None = None           # 별표 N
    attach_title: str | None = None
    attach_related: str | None = None
    seq = 0                                     # 별표 단서·표·부칙의 id 꼬리

    def flush() -> None:
        nonlocal article
        if article is None:
            return
        elements.append(Article(
            id=f"{doc_id}#{article['section']}", section=article["section"],
            title=article["title"], head=article["head"], paragraphs=article["paragraphs"],
            chars=_chars(article["head"], article["paragraphs"])))
        counts["조"] += 1
        article = None

    for node in box.find_all(["h4", "h5", "p", "ol", "table"]):
        # 표 안쪽의 p·ol 은 표가, 중첩 ol 은 항이 가져간다
        if node.name != "table" and node.find_parent("table") is not None:
            continue
        if node.name == "ol" and node.find_parent("ol") is not None:
            continue
        if node.name == "p" and node.find_parent("li") is not None:
            continue
        text = _text(node)
        if not text:
            continue

        if node.name == "h4":
            flush()
            if _RE_SUPPLEMENTARY.match(text):
                supplementary, attach_section = text, None
                continue
            supplementary = None
            m = _RE_CHAPTER.match(text)
            elements.append(Heading(
                id=f"{doc_id}#제{m.group(1)}장" if m else f"{doc_id}#{text[:20]}",
                level=1, text=text, section=f"제{m.group(1)}장" if m else None))
            counts["장"] += 1

        elif node.name == "h5":
            flush()
            if m := _RE_ARTICLE.match(text):
                section = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
                article = {"section": section, "head": text, "paragraphs": [],
                           "title": text[m.end():].strip().strip("()（）") or None}
            else:                                # 별표 제목
                attach_related = r.group(1) if (r := _RE_RELATED.search(text)) else None
                attach_title = _RE_RELATED.sub("", text).strip()

        elif node.name == "p":
            if m := _RE_ATTACH.match(text):
                flush()
                supplementary = None
                attach_section = f"별표 {m.group(1)}"
                counts["별표"] += 1
            elif supplementary is not None:
                seq += 1
                elements.append(Para(id=f"{doc_id}#부칙-{seq}", section="부칙",
                                     title=supplementary, text=text))
                counts["부칙"] += 1
            elif article is not None:
                article["paragraphs"].append(Paragraph(text=text))
            elif attach_section is not None:
                # 별표에 딸린 단서. Para 로 내면 조문형 문서에서 청커가 버린다 (RAG-034 ④)
                seq += 1
                head = f"{attach_section} {text}"     # 인용에 별표 번호가 남게 앞에 붙인다
                elements.append(Article(
                    id=f"{doc_id}#{attach_section}-note{seq}", section=attach_section,
                    title=attach_title, head=head, chars=len(head)))
            else:
                counts["머리말 문단"] += 1        # 제정·개정 이력 — published_at 으로만 쓴다

        elif node.name == "ol":
            if article is None:
                continue
            paras = _paragraphs(node)
            head = article["paragraphs"]
            if head and not head[-1].sym and not head[-1].items and not any(p.sym for p in paras):
                # `제3조(정의)` 처럼 두문 `<p>` + 호 `<ol>` — 항이 아니라 앞 문단의 호다
                head[-1].items = [
                    Item(no=(m.group(1) if (m := _RE_ITEM.match(p.text)) else ""),
                         text=p.text,
                         subitems=[SubItem(no=i.no, text=i.text) for i in p.items])
                    for p in paras
                ]
            else:
                article["paragraphs"] += paras

        else:                                    # table
            header, rows = htmltable.header_and_rows(node)
            cap = node.find("caption")
            caption = _text(cap) if cap else (attach_title or "")
            related = r.group(1) if (r := _RE_RELATED.search(caption)) else attach_related
            seq += 1
            elements.append(Table(
                id=f"{doc_id}#{attach_section or '표'}-{seq}",
                title=_RE_RELATED.sub("", caption).strip() or (attach_title or ""),
                section=attach_section, header=header, rows=rows, related=related))
    flush()

    published = revision_date(_text(box)) or doc.meta.get("published_at")
    return Parsed(elements=elements, document_title=title, published_at=published,
                  citation_url=doc.meta.get("source_url"), counts=counts)
