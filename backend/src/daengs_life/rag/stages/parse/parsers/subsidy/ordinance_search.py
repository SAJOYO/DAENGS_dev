"""자치법규(조례) Open API(DRF) XML 파서 — 조문 · 부칙 (RAG-004, RAG-019).

`parsers/law/law_drf_api.py` 와 **같은 API 의 다른 target 이지만 규격이 겹치지 않는다.**
법령은 `<조문단위>/<항>/<호>/<목>/<별표단위>`, 조례는 `<조문>/<조>` 하나뿐이다. 그래서
법령 파서를 상속하거나 공통 베이스로 묶지 않았다 — 겹치는 것은 날짜 정규화 정도이고,
그것을 위해 두 규격을 아는 베이스를 만들면 나중에 어느 쪽을 고쳐도 다른 쪽이 흔들린다.
(수집 쪽에서 실제로 공유되는 **호출 규약**만 `crawler/sources/_drf.py` 로 올라가 있다.)

이 파서가 다루는 원본의 특성 (2026-08-27 실측 — 30개 문서 348개 조 표본)
  - `조문여부` `Y`=조문(344) / `N`=장·절 제목(4). 법령의 `조문`/`전문` 과 값만 다르다.
    `N` 일 때 `조문번호` 는 `000000` 이라 번호에서 섹션을 못 만든다 → 조내용에서 뽑는다
  - `조문번호` 는 **6자리 고정, 앞 4 = 조 · 뒤 2 = 가지번호**(`000100` = 제1조,
    `001203` = 제12조의3). 348건 전부 조내용 앞머리와 일치했다. 법령 쪽이 `조문번호`+
    `조문가지번호` 두 태그로 나눠 주던 것을 여기서는 한 필드에서 잘라 쓴다
  - **항·호·목 태그가 없다.** "① …", "1. …" 가 `<조내용>` 안의 줄바꿈 텍스트다.
    따라서 `Article.paragraphs` 는 항상 비고, 조문 전체가 `head` 에 들어간다.
    청크 ID 가 `제N조` 까지만 내려가고 `제N조제2항` 은 못 만든다 — 조례는 조가 짧아
    (표본 평균 2,750자/10.3조 ≈ 조당 270자) 2,000자 분할에 거의 걸리지 않는다
  - 별표·서식이 없다. 법령 파서의 `boxtable` 경로가 통째로 필요 없다
  - `<부칙>` 은 문서당 하나이고 `부칙내용` 도 하나다 (법령은 `<부칙단위>` 가 개정마다 쌓인다)

출처 링크 — API 주소는 키가 `***` 로 마스킹돼 사람이 못 연다(RAG-012). 법령 파서가
`law.go.kr/법령/{법령명}` 을 쓰는 것과 같은 규칙으로 `law.go.kr/자치법규/{자치법규명}` 을 쓴다.
공백을 지운 이름으로 200 이 오는 것을 확인했다. `ordinInfoR.do?ordinSeq=` 쪽이 서버렌더 본문을
바로 주지만 그 일련번호는 개정마다 바뀌어 링크가 늙으므로 쓰지 않는다.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Article, Heading, Para
from ..base import Parsed

NAME = "ordinance_xml"
VERSION = 1

ORDIN_URL = "https://www.law.go.kr/자치법규/"

_WS = re.compile(r"\s+")
# 장·절에도 가지번호가 붙을 수 있다 — 법령 파서(_RE_DIVISION)와 같은 이유로 `의2` 를 살린다.
_RE_DIVISION = re.compile(r"^(제\s*\d+\s*[편장절관](?:\s*의\s*\d+)?)")
# `부칙 <조례 제2630호, 2018.12.04.>` — 개정별 부칙의 머리줄. `<` 까지 봐야 본문의 "부칙" 언급과 안 섞인다
_RE_ADDENDUM_HEAD = re.compile(r"^부\s?칙\s*<")


def _clean(s: str | None) -> str:
    return _WS.sub(" ", s).strip() if s else ""


def _text(node, *names: str) -> str:
    for n in names:
        el = node.find(n) if node is not None else None
        if el is not None:
            if got := _clean(el.get_text()):
                return got
    return ""


def _lines(node, *names: str) -> list[str]:
    """줄바꿈을 살려서 읽는다. 조례는 항·호가 태그가 아니라 줄이라 여기서만 원문 줄이 남는다."""
    for n in names:
        el = node.find(n) if node is not None else None
        if el is not None:
            out = [c for line in el.get_text().split("\n") if (c := _clean(line))]
            if out:
                return out
    return []


def _ymd(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else (raw or None)


def _addenda(soup) -> list[list[str]]:
    """`부칙내용` 을 개정별로 쪼갠다. 경계는 `부칙 <법률 제N호, YYYY. M. D.>` 머리줄이다.

    원문은 `부    칙 <…>` 처럼 사이에 공백이 들어 있는데 `_clean` 이 이미 한 칸으로 줄여
    놓으므로 `부 칙` 과 `부칙` 만 보면 된다. 머리줄이 하나도 없으면(단일 부칙) 통째로 한 묶음이다.
    """
    out: list[list[str]] = []
    for add in soup.find_all("부칙"):
        for line in _lines(add, "부칙내용"):
            if _RE_ADDENDUM_HEAD.match(line) or not out:
                out.append([line])
            else:
                out[-1].append(line)
    return out


def _section(unit) -> str | None:
    """`000100` → 제1조, `001203` → 제12조의3. 6자리가 아니면 규격이 바뀐 것이라 None."""
    raw = re.sub(r"\D", "", _text(unit, "조문번호"))
    if len(raw) != 6:
        return None
    no, branch = int(raw[:4]), int(raw[4:])
    return f"제{no}조" + (f"의{branch}" if branch else "") if no else None


# ------------------------------------------------------------------ 진입점
def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "xml")
    info = soup.find("자치법규기본정보")
    if info is None:
        raise RuntimeError("자치법규기본정보 태그 없음 — 응답 규격이 다르다")

    title = _text(info, "자치법규명") or doc.meta.get("document_title", "")
    doc_id = doc.doc_id
    elements: list = []
    warnings: list[str] = []
    n_articles = 0

    for unit in soup.find_all("조"):
        lines = _lines(unit, "조내용")
        if not lines:
            continue
        head = lines[0]

        if _text(unit, "조문여부") != "Y":
            # 장·절 제목. 경계 표시용이라 청킹하지 않지만 문서 순서에는 남긴다.
            # 이때 조문번호가 000000 이라 섹션은 본문에서 읽어야 한다
            sec = m.group(1).replace(" ", "") if (m := _RE_DIVISION.match(head)) else None
            elements.append(Heading(id=f"{doc_id}#{sec or head[:20]}", level=1,
                                    text=head, section=sec))
            continue

        section = _section(unit)
        if section is None:
            # 번호를 못 읽으면 조를 통째로 버리는 대신 순서 기반 id 로 살린다.
            # 조용히 사라지는 것이 규격 변화를 가장 늦게 알아채는 길이다
            n_articles += 1
            section = f"제{n_articles}조?"
            warnings.append(f"조문번호를 읽지 못함 → {section} 으로 대체: {head[:30]}")
        else:
            n_articles += 1

        # 항·호가 태그가 아니라 줄이라 조문 전체가 head 로 들어간다. 줄바꿈은 살린다 —
        # 호가 한 줄로 뭉치면 답변에 인용할 때 어디까지가 한 호인지 사라진다
        body = "\n".join(lines)
        elements.append(Article(
            id=f"{doc_id}#{section}", section=section,
            title=_text(unit, "조제목") or None,
            head=body, paragraphs=[], chars=len(body),
            key=unit.get("조문번호")))

    if not n_articles:
        raise RuntimeError("조문이 하나도 없다 — 응답 규격이 다르다")

    # 부칙 — **`<부칙>` 태그는 하나지만 그 안에 개정별 부칙이 전부 들어 있다.**
    # 법령은 `<부칙단위>` 가 개정마다 따로 오는데 조례는 `부칙내용` 한 덩어리다. 통째로 한
    # 문단에 넣으면 청커가 그 안의 "제2조" 를 보고 `#부칙-1제2조` 를 만드는데, 개정마다 제2조가
    # 있어 **한 문서 안에서 chunk_id 가 겹친다** (실측 5건). 청크 주소는 골든셋이 가리키는
    # 값이라 겹치면 라벨이 어느 쪽을 뜻하는지 사라진다 — 그래서 법령과 같은 단위로 쪼갠다.
    for i, group in enumerate(_addenda(soup), 1):
        elements.append(Para(id=f"{doc_id}#부칙-{i}", title=group[0],
                             text="\n".join(group[1:]) or group[0], section="부칙"))

    return Parsed(
        elements=elements,
        document_title=title,
        published_at=doc.meta.get("published_at") or _ymd(_text(info, "시행일자")),
        citation_url=ORDIN_URL + quote(title.replace(" ", "")),
        counts={"articles": n_articles},
        warnings=warnings,
        extra={"org": _text(info, "지자체기관명"),
               "promulgated_at": _ymd(_text(info, "공포일자")),
               "promulgation_no": _text(info, "공포번호"),
               "revision_kind": _text(info, "제개정정보")},
    )
