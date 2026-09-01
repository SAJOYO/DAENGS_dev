"""찾기쉬운 생활법령정보 "반려동물과 생활하기" 파서 — 해설 본문 + 100문100답 (RAG-004, RAG-018).

**계층이 클래스명에 그대로 있다** (2026-08-21 실측, 본문 7페이지):

  plv1a   인쇄체크 반려견이 맹견이라면?          대단위 ~2,000자 / 주제 4개  → heading level 1
  plv2a     나의 반려견이 맹견일까?              소제목  ~700자 / 주제 1개  → heading level 2 ★청킹 단위
  plv3a       "맹견"이란 다음의 어느 하나에…      본문                      → para
  plv4d         5. 로트와일러와 그 잡종의 개      본문 (질문 4의 정답)        → para
  tplv4   ※ 반려동물과 정부 지정 자연공원 이용하기  강조 박스 (질문 7의 정답)   → aside

RAG-004 가 `인쇄체크` 대단위가 아니라 **소제목(plv2a)** 을 청킹 단위로 확정했다. 대단위는 한 덩어리에
목줄+인식표+배설물+자연공원 네 주제가 섞여 기준③(한 청크 = 한 주제)을 깬다.

강조 박스를 별도 요소로 두는 이유 — 제목이 따로 있고(`※ …`) 주제도 독립적이다. 앞 소제목에
붙여 버리면 질문 7("국립공원에 강아지 가능한가요")의 정답이 배설물 수거 이야기에 묻힌다.

Q/A 탭(`.ovDivbox1`)은 구조가 아예 다르다 — `ul.question` 하나가 질문/답 한 쌍이고,
바로 뒤 `table.normal.st5` 에 관련법령이 붙는다. 쪼개면 둘 다 쓸모없어지므로 통짜 1청크다.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from daengs_life.crawler.core import textutil            # 「법령」 조항 인용 파싱 — 한국 법령 문서 공통 지식
from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Aside, Heading, Para, QA
from ..base import Parsed

NAME = "easylaw_html"
VERSION = 1

_WS = re.compile(r"\s+")
_RE_PRINT_CHECK = re.compile(r"^\s*인쇄체크\s*")
_RE_MARKER = re.compile(r"^\s*([※☞√•▶◇]|\d+\.|[가-힣]\.|\(\d+\))")
# 제목·문장이 <strong> 여러 개로 쪼개져 있어 get_text(" ") 가 괄호·마침표 앞뒤에 공백을 만든다
# ("목줄 ( 가슴줄 ) ·인식표 … 주세요 ."). 조항 인용의 공백("제16조 제2항")은 건드리지 않는다.
_RE_SPACE_BEFORE = re.compile(r"\s+([)\]}」』,.:;·%])")
_RE_SPACE_AFTER = re.compile(r"([(\[{「『])\s+")
_NOISE_SELECTORS = "script, style, .copyImgAddress, .info_box, label.labelnone"


def _text(node) -> str:
    """인라인 `<a>` 로 감싼 조항 인용이 끊기지 않게 공백으로 잇는다."""
    t = _WS.sub(" ", node.get_text(" ")).strip()
    t = _RE_SPACE_AFTER.sub(r"\1", _RE_SPACE_BEFORE.sub(r"\1", t))
    return _RE_PRINT_CHECK.sub("", t).strip()


def _plv_level(classes: list[str]) -> int | None:
    """`plv3a` → 3. easylaw 는 들여쓰기 깊이를 클래스 이름에 넣는다."""
    for c in classes:
        if m := re.fullmatch(r"plv(\d+)[a-z]?", c):
            return int(m.group(1))
    return None


# ------------------------------------------------------------------ 본문
def _parse_body(soup: BeautifulSoup, doc_id: str) -> list:
    box = soup.select_one("#ovDiv .ovDivbox")
    if box is None:
        raise RuntimeError("본문 컨테이너(#ovDiv .ovDivbox)가 없다 — 페이지 구조가 바뀌었다")
    for tag in box.select(_NOISE_SELECTORS):
        tag.decompose()

    out: list = []
    n = {"h1": 0, "h2": 0, "p": 0, "note": 0}

    for div in box.find_all("div", recursive=False):
        classes = div.get("class") or []
        site_id = div.get("id")

        if "tplv4" in classes:                          # 강조 박스 — 안쪽 tplv2* 가 줄이다
            lines = [t for inner in div.select("[class^=tplv2]") if (t := _text(inner))]
            if not lines:
                # 안쪽 tplv2* 없이 내용이 바로 들어 있는 박스가 하나 있다 (1-1-1 입양 체크리스트).
                # 블록 태그 경계로 줄을 나눈다 — 통짜로 두면 항목 구분이 사라진다
                lines = [ln for ln in textutil.block_text(div).split("\n") if ln.strip()]
            if not lines:
                continue
            n["note"] += 1
            out.append(Aside(id=f"{doc_id}#note-{n['note']}", title=lines[0],
                             lines=lines[1:] or lines, site_id=site_id))
            continue

        level = _plv_level(classes)
        if level is None:                               # spaceDiv1/2, cnpClsTitle 등
            continue
        text = _text(div)
        if not text:
            continue

        if level <= 2:
            key = f"h{level}"
            n[key] += 1
            out.append(Heading(id=f"{doc_id}#{key}-{n[key]}", level=level, text=text,
                               site_id=site_id))
        else:
            n["p"] += 1
            marker = m.group(1) if (m := _RE_MARKER.match(text)) else None
            out.append(Para(id=f"{doc_id}#p-{n['p']}", text=text, level=level, marker=marker,
                            cites=textutil.cites(text), site_id=site_id))
    return out


# ------------------------------------------------------------------ 100문100답
def _parse_qna(soup: BeautifulSoup, doc_id: str) -> list:
    box = soup.select_one(".ovDivbox1")
    for tag in box.select(_NOISE_SELECTORS):
        tag.decompose()

    out: list = []
    for i, ul in enumerate(box.select("ul.question"), 1):
        title_li = ul.select_one("li.title")
        if title_li is not None:
            for p in title_li.find_all("p"):            # "조회수: 3,163건 추천수: 580건"
                p.decompose()
        spans = title_li.select("span") if title_li else []
        title = _text(spans[-1]) if spans else ""

        dt, dd = ul.select_one("dl.q dt"), ul.select_one("dl.q dd")
        if dt is None or dd is None:
            continue
        answer = "\n".join(t for div in dd.find_all("div") if (t := _text(div)))

        related_laws: list[str] = []
        topic = None
        table = ul.find_next_sibling("table")
        if table is not None:
            for tr in table.select("tr"):
                th, td = tr.find("th"), tr.find("td")
                if th is None or td is None:
                    continue
                label = _text(th)
                if "관련법령" in label:
                    # 「법령명」 제○조 형태를 RAG-004 section 형식으로 정규화한다
                    for para in td.find_all("p") or [td]:
                        related_laws += [c for c in textutil.cites(_text(para))
                                         if c not in related_laws]
                elif "관련생활분야" in label:
                    topic = _text(td)

        out.append(QA(id=f"{doc_id}#qa-{i}", title=title, question=_text(dt), answer=answer,
                      related_laws=related_laws, related_topic=topic))
    return out


# ------------------------------------------------------------------ 진입점
def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "lxml")
    # 어느 탭인지는 slug 가 아니라 **컨테이너 존재 여부**로 판정한다 — 파일명 규칙이 바뀌어도 버틴다.
    # 주의: `.ovDivbox1` 은 본문 페이지에도 있는 바깥 래퍼라 판정 기준이 못 된다.
    # 본문에만 있는 `#ovDiv .ovDivbox` 로 가른다 (Q/A 탭은 이 조합이 없다).
    is_qna = soup.select_one("#ovDiv .ovDivbox") is None
    elements = (_parse_qna if is_qna else _parse_body)(soup, doc.doc_id)

    title_tag = soup.find("title")
    title = ((title_tag.get_text(" ", strip=True) if title_tag else "").split("|")[0].strip()
             or doc.meta.get("document_title", ""))

    return Parsed(
        elements=elements,
        document_title=title,
        published_at=doc.meta.get("published_at"),
        # 원본을 받은 곳이 곧 사람이 열 페이지다. 법령 API 와 달리 바꿔칠 것이 없다 (RAG-019)
        citation_url=doc.meta.get("source_url"),
        counts={"qna": int(is_qna)},
    )
