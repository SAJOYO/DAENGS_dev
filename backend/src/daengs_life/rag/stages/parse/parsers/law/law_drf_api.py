"""법령 Open API(DRF) XML 파서 — 조문/항/호/목 · 부칙 · 별표 (RAG-004, RAG-019, RAG-020).

원본이 `<조문단위>/<항>/<호>/<목>/<별표단위>` 로 구조를 이미 명시한다. 경계를 그 태그에 맞추면
청크 ID 가 곧 인용 문자열이 된다 (`제15조제2항제1호`). 텍스트로 뭉개면 그 복원이 불가능하다.

이 파서가 다루는 원본의 특성 (2026-08-20~21 실측)
  - `조문여부` 가 `전문` 인 단위는 장·절 제목("제1장 총칙")이다 → `heading`, 조문 수에 넣지 않는다
  - `조문가지번호` 179건. **패딩 없는 정수**라 `조문번호=12` + `가지번호=3` → `제12조의3`
  - 항·호·목 내용에는 번호가 이미 붙어 있다(`① …`, `1. …`) — 따로 조립하지 않는다
  - 제2조처럼 **번호 없는 항**이 호를 감싸는 경우가 있다 (`<항>` 안에 `항번호` 가 없다)
  - 조문 텍스트에 한자 이미지 `<img src="…flDownload.do…">` 가 3건 박혀 있다 → 지우고 빈 괄호도 정리
  - `별표구분` 은 `별표` 69 / `서식` 126. **서식은 빈 양식이라 인덱싱하지 않는다** (RAG-004)

출처 링크 — API 호출 주소는 키가 `***` 로 마스킹돼 사람이 못 연다(RAG-012). `law.go.kr/법령/{법령명}`
형식이 8종 전부 200 을 주는 것을 확인해 그것을 `citation_url` 로 쓴다. 웹 원문(RAG-011)을 수집하지
않은 수의사법·자연공원법까지 한 규칙으로 덮이고, 항상 현행을 가리킨다.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from daengs_life.rag.stages.parse.extract import boxtable
from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Aside, Article, Heading, Item, Para, Paragraph, SubItem, Table
from ..base import Parsed

NAME = "law_xml"
VERSION = 1

LAW_URL = "https://www.law.go.kr/법령/"

_WS = re.compile(r"\s+")
# CDATA 안에 `부저병(<img src="…flDownload.do…" alt="…">腐?病</img>)` 형태로 들어 있다.
# 여는 태그만 지우면 `</img>` 가 본문에 남는다. 안쪽 한자 텍스트는 원문이므로 살린다
# (`?` 는 법제처 쪽에서 이미 깨진 글자라 우리가 만든 손실이 아니다).
_IMG = re.compile(r"</?img[^>]*>")
_EMPTY_PAREN = re.compile(r"[(（]\s*[)）]")
_CIRCLED = {chr(0x2460 + i): i + 1 for i in range(20)}          # ① … ⑳
# 장·절에도 가지번호가 붙는다 — `제4장의2 지질공원`, `제2장의2 동물보건사`.
# `의2` 를 빼면 `제4장` 과 id 가 겹쳐 요소 하나가 조용히 덮인다.
_RE_DIVISION = re.compile(r"^(제\s*\d+\s*[편장절관](?:\s*의\s*\d+)?)")
_RE_RELATED = re.compile(r"[(（]([^)）]*관련)[)）]\s*$")
_RE_ATTACH_HEAD = re.compile(r"^■.*\[(별표|서식)[^\]]*\]")


def _clean(s: str | None) -> str:
    """한자 이미지 태그 제거 → 남은 빈 괄호 정리 → 공백 정규화."""
    if not s:
        return ""
    return _WS.sub(" ", _EMPTY_PAREN.sub("", _IMG.sub("", s))).strip()


def _text(node, *names: str) -> str:
    for n in names:
        el = node.find(n) if node is not None else None
        if el is not None:
            got = _clean(el.get_text())
            if got:
                return got
    return ""


def _ymd(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else (raw or None)


def _int(raw: str) -> int | None:
    d = re.sub(r"\D", "", raw or "")
    return int(d) if d else None


# ------------------------------------------------------------------ 조문
def _article_section(unit) -> str:
    """제15조 / 제12조의3. 가지번호는 패딩 없는 정수다."""
    no = _text(unit, "조문번호")
    branch = _int(_text(unit, "조문가지번호"))
    return f"제{no}조" + (f"의{branch}" if branch else "")


def _paragraphs(unit) -> list[Paragraph]:
    out: list[Paragraph] = []
    for hang in unit.find_all("항", recursive=False):
        sym = _text(hang, "항번호") or None
        items: list[Item] = []
        for ho in hang.find_all("호", recursive=False):
            subs = [SubItem(no=_text(mok, "목번호"), text=_text(mok, "목내용"))
                    for mok in ho.find_all("목", recursive=False)]
            items.append(Item(no=_text(ho, "호번호"), text=_text(ho, "호내용"), subitems=subs))
        out.append(Paragraph(no=_CIRCLED.get(sym or ""), sym=sym,
                             text=_text(hang, "항내용"), items=items))
    return out


def _chars(head: str, paras: list[Paragraph]) -> int:
    """청커의 2,000자 판정 입력 (RAG-004). 조문 전체가 몇 자인지."""
    n = len(head)
    for p in paras:
        n += len(p.text) + sum(len(i.text) + sum(len(s.text) for s in i.subitems) for i in p.items)
    return n


# ------------------------------------------------------------------ 별표
def _attachment_section(unit) -> str:
    no = _int(_text(unit, "별표번호"))
    branch = _int(_text(unit, "별표가지번호"))
    kind = _text(unit, "별표구분") or "별표"
    return f"{kind} {no}" + (f"의{branch}" if branch else "")


def _attachment_files(unit) -> dict[str, str]:
    """원본 HWP/PDF 링크. 표가 깨졌을 때 사람이 확인할 수 있는 마지막 수단이라 버리지 않는다."""
    out: dict[str, str] = {}
    for tag, key in (("별표HWP파일명", "hwp"), ("별표PDF파일명", "pdf")):
        if name := _text(unit, tag):
            out[key] = name
    for tag, key in (("별표서식파일링크", "hwp_url"), ("별표서식PDF파일링크", "pdf_url")):
        if link := _text(unit, tag):
            out[key] = "https://www.law.go.kr" + link
    return out


def _attachment(unit, doc_id: str, warnings: list[str]) -> list:
    """별표 하나 → 텍스트 문단(Para) + 표(Table) + 도식(Aside) 나열."""
    section = _attachment_section(unit)
    raw_title = _text(unit, "별표제목")
    related = m.group(1) if (m := _RE_RELATED.search(raw_title)) else None
    title = _RE_RELATED.sub("", raw_title).strip()
    files = _attachment_files(unit)

    out: list = []
    unit_note: str | None = None
    seq = 0
    for block in boxtable.parse(unit.find("별표내용").get_text()):
        seq += 1
        eid = f"{doc_id}#{section}-{seq}"
        if isinstance(block, boxtable.TextBlock):
            if block.degraded:
                # 격자를 못 세운 괘선 구간. 글자가 없으면 괘선으로 그린 빈 양식(꼬리표·봉함표)이라
                # 버리고, 글자가 있으면 **구조만 잃고 내용은 살린다** — 조용히 사라지는 것이
                # 가장 나쁜 실패다. 경고를 남겨 나중에 표 파서를 고칠 단서로 쓴다
                if not block.paragraphs:
                    seq -= 1
                    continue
                warnings.append(f"{section}: 격자를 못 세운 괘선 구간 → 구조 없이 aside 로 보존")
                out.append(Aside(id=eid, title=title, section=section, lines=block.paragraphs))
                continue
            keep = []
            for para in block.paragraphs:
                if _RE_ATTACH_HEAD.match(para):        # `■ …[별표 4] <개정 …>` — section 과 중복
                    continue
                if para.startswith("(단위"):           # 표 전체에 한 번만 나온다 → Table 로 옮긴다
                    unit_note = para
                    continue
                keep.append(para)
            if not keep:
                seq -= 1
                continue
            out.append(Para(id=eid, text="\n".join(keep), section=section, title=title))
        elif block.n_cols > 1:
            out.append(Table(id=eid, title=title, section=section, unit=unit_note,
                             header=block.header, rows=block.rows, related=related, files=files))
        else:
            # 헤더/본문 구분이 없는 단일 칸 박스 — 과징금 산식 같은 도식이라 버리면 안 된다 (RAG-020)
            out.append(Aside(id=eid, title=title, section=section,
                             lines=[c for row in block.rows for c in row if c]))
    return out


# ------------------------------------------------------------------ 진입점
def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "xml")
    info = soup.find("기본정보")
    if info is None:
        raise RuntimeError("기본정보 태그 없음 — 응답 규격이 다르다")

    title = _text(info, "법령명_한글", "법령명한글", "법령명") or doc.meta.get("document_title", "")
    doc_id = doc.doc_id
    elements: list = []
    warnings: list[str] = []
    skipped_forms = 0

    for unit in soup.find_all("조문단위"):
        head = _text(unit, "조문내용")
        if _text(unit, "조문여부") != "조문":
            # 장·절 제목. 경계 표시용이라 청킹하지 않지만 문서 순서에는 남긴다
            sec = m.group(1).replace(" ", "") if (m := _RE_DIVISION.match(head)) else None
            elements.append(Heading(id=f"{doc_id}#{sec or head[:20]}", level=1,
                                    text=head, section=sec))
            continue
        section = _article_section(unit)
        paras = _paragraphs(unit)
        elements.append(Article(
            id=f"{doc_id}#{section}", section=section, title=_text(unit, "조문제목") or None,
            head=head, paragraphs=paras, chars=_chars(head, paras),
            key=unit.get("조문키"), effective_date=_ymd(_text(unit, "조문시행일자"))))

    # 부칙 — 시행일과 경과규정이 들어 있다. 조문과 규칙이 달라 통째로 한 문단으로 둔다
    for add in soup.find_all("부칙단위"):
        lines = [l for l in (_clean(x) for x in add.find("부칙내용").get_text().split("\n")) if l]
        if not lines:
            continue
        elements.append(Para(id=f"{doc_id}#부칙-{add.get('부칙키')}", title=lines[0],
                             text="\n".join(lines[1:]) or lines[0], section="부칙"))

    for att in soup.find_all("별표단위"):
        if _text(att, "별표구분") == "서식":
            skipped_forms += 1                 # 빈 양식 텍스트라 검색 노이즈다 (RAG-004)
            continue
        elements.extend(_attachment(att, doc_id, warnings))

    return Parsed(
        elements=elements,
        document_title=title,
        published_at=doc.meta.get("published_at") or _ymd(_text(info, "시행일자")),
        citation_url=LAW_URL + quote(title),
        counts={"skipped_forms": skipped_forms},
        warnings=warnings,
        extra={"promulgated_at": _ymd(_text(info, "공포일자")),
               "promulgation_no": _text(info, "공포번호"),
               "ministry": _text(info, "소관부처")},
    )
