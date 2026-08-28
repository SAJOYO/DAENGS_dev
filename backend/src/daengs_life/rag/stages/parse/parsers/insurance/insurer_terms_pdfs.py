"""보험사 약관 PDF 파서 — 포맷 층(`extract/pdf.py`)에 얹는 사이트 층 (RAG-018).

**이 파일이 아는 것은 "보험사 공시실에서 받은 약관"이라는 것뿐이다.** PDF 를 조·표로 펴는
일은 포맷 층이 하고, 여기는 문서 제목·출처 링크·`published_at` 처럼 그 소스라야 아는 것만
채운다. `korail_terms.py` 와 나란한 자리이고, 포맷 층은 **그대로 쓴다** — 분기가 필요해지면
그건 #49 가 IR 을 안 지켰다는 신호다 (RAG-031 ④).

⚠️ **`pdf` 그룹(PyMuPDF, AGPL)이 필요하다.** 없으면 무엇을 해야 하는지 알려 주고 실패한다.
조용히 0건으로 끝나면 RAG-030 ① 이 겪은 그 소실이 된다.

────────────────────────────────────────────────────────────────────────────
코레일과 다른 점
────────────────────────────────────────────────────────────────────────────
**① 제목을 상수표에서 못 준다.** 코레일은 문서가 두 벌이라 `DOCS` 에 박아 뒀는데, 약관은
**상품마다 다르고 개정마다 늘어난다.** 크롤러가 목록에서 읽은 상품명이 `.meta.json` 의
`document_title` 로 이미 와 있으므로 그것을 쓴다 — 상수표를 두면 상품이 늘 때마다 파서를
고쳐야 하고, 안 고치면 "모르는 문서다" 로 수집이 멈춘다.

**② 합본이 아니다.** 코레일 여객운송약관은 두 약관의 합본이라 조 번호가 재시작했지만
(RAG-036 ①), 보험약관은 상품 하나에 약관 하나다. 포맷 층의 합본 처리는 **그대로 두면
발동하지 않는다** — 끄지 않는 이유는, 특별약관이 본문 안에 `○○특별약관` 으로 붙는 판이
나오면 그때 저절로 필요해지기 때문이다.

**③ 분량이 한 자릿수 배 크다.** 코레일이 22p·44p 였는데 보험약관은 **132~231p** 다
(판매중 11건 · 합계 1,530p · 표 494개, 2026-08-28 실측). 조 하나가 길어 청크 상한
(RAG-004 2,000자)에 걸리는 조가 나올 수 있는데, **막지 않고 경고만** 남긴다 — 청커가
항 단위로 나누는 경로를 이미 갖고 있다.

**④ 스캔은 없었다.** 0자 페이지 18/1,530 = 1.2% 이고 전부 간지다. RAG-032 ③ 의
"표본 4개에서 0건" 이 11건에서도 유지된다.

출처 링크 — PDF 직링크(`/publication/pdf/{상품코드}_0_{판매개시일}_file1.pdf`)는 **개정마다
바뀐다.** 실측에서 위풍댕댕이 `ZPB316050_0_20240401` → `ZPB316090_0_20260701` 로 상품코드까지
바뀌었다. 그래서 답변에는 **공시실 주소**를 싣는다 — 사람이 열면 현행 약관이 거기 있고 그
주소는 개정과 무관하다. 코레일이 `fileNo` 대신 목록 페이지를 고른 것과 같은 판단(RAG-036).
"""
from __future__ import annotations

import re

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.stages.parse.extract import pdf
from ..base import Parsed

NAME = "insurer_terms_pdf"
VERSION = 2

# ── 약관 본문의 앞뒤를 끊는 마커 (11건 전수 검증, 2026-08-28)
#
# **머리** — 본문은 `…보통약관` 표제 한 줄이 놓인 **얇은 페이지**에서 시작한다. 목차에도 같은
# 문자열이 있어서 문자열만으로는 못 가른다. 표제 페이지는 줄이 1~2개이고 목차 페이지는 100줄이
# 넘는다 — **페이지의 두께가 가르는 축이다.** 실측 시작 쪽: 소형 3건 p3 · 대형 8건 p21~p33.
#
# **꼬리** — 부록이 두 겹이다. `별표` 한 줄짜리 표제가 먼저 오고(`[별표1] 보험금을 지급할
# 때의 적립이율 계산` …), 그 뒤에 `[법규1] 의료법` 처럼 `[법규N]` 표제가 붙은 관계법령 전문이
# 온다. **둘 중 먼저 오는 데서 끊는다** — `[법규N]` 만 보면 그 사이의 별표가 남아, 대형 문서에서
# `제도성 특별약관 제8조` 가 37,771자를 삼켰다(실측). `별표` 는 대형 8건에만 있고 소형 3건에는
# 없어서, `[법규N]` 이 그때의 유일한 마커다 — 그래서 둘 다 본다.
_RE_BOTONG = re.compile(r".*보통약관$")
_RE_APPENDIX = re.compile(r"^별\s?표$")
_RE_LAW_APPENDIX = re.compile(r"^\[법규\s*\d+\]")
_THIN_PAGE = 10                          # 표제 페이지의 줄 수 상한. 목차는 100줄이 넘는다

# 보험 특별약관 이름은 **공백이 있고 길다** — `반려묘 수술비(치과및구강질환포함)
# 확대보장(재가입형) 특별약관`(34자). 포맷 층 기본값(공백 없는 4~30자)으로는 0개를 잡는다.
#
# ⚠️ **`약관` 이 아니라 `보통약관`·`특별약관` 으로 끝나는 줄만 받는다.** 그냥 `약관` 까지
# 열어 두면 `보장내용 등 필요한 사항을 정한 약관` 같은 **본문 문장**이 경계가 된다 (실측).
# 앞의 `(?![①-⑳\d])` 는 항 번호로 시작하는 문장(`① … 이 특별약관`)과 목차 줄(`1-1. …`)을
# 함께 막는다 — 포맷 층 기본값이 쓰던 그 장치를 그대로 가져왔다.
_RE_INSURANCE_TERMS = re.compile(r"^(?![①-⑳\d])(?=.{4,60}$).*(?:보통약관|특별약관)$")

# 크롤러가 `citation_url` 을 못 넣었을 때만 쓴다 (삼성 기준)
_FALLBACK_ROOM = "https://www.samsungfire.com/vh/page/VH.REIF0011.do"


def _open(raw: bytes):
    try:
        import pymupdf
    except ImportError as e:                  # pragma: no cover - 그룹이 있으면 안 탄다
        raise RuntimeError(
            "PyMuPDF 가 없다. PDF 파싱은 `pdf` 그룹에 있다 (AGPL 이라 일부러 갈라 뒀다 — RAG-032 ②).\n"
            "  backend/ 에서: uv sync --group pdf\n"
            "  ⚠️ 인자 없는 `uv sync` 는 exact 동기화라 다른 그룹을 지운다. 그룹을 명시할 것."
        ) from e
    return pymupdf.open(stream=raw, filetype="pdf")


def _body_pages(pdf_doc) -> range:
    """약관 본문의 페이지 범위 (0-based). 앞의 안내 책자와 뒤의 관계법령을 뺀다.

    못 찾으면 **그 끝은 자르지 않는다** — 자르는 쪽이 안전해 보이지만, 마커가 사라진 것은
    문서 구조가 바뀌었다는 뜻이라 그때 임의로 자르면 약관 본문을 통째로 날릴 수 있다.
    전부 읽고 경고를 남기는 편이 낫다 (호출부가 `warnings` 로 받는다).
    """
    # ⚠️ `start` 를 0 으로 두고 `if start` 로 판정하면 **표제가 첫 쪽일 때 못 찾은 것과
    # 구분이 안 된다** — 그러면 꼬리 마커를 아예 안 보게 되어 관계법령이 그대로 들어온다.
    # 테스트가 이걸 잡았다 (`test_cut_at_laws_when_there_is_no_annex`).
    start: int | None = None
    end = pdf_doc.page_count
    for i in range(pdf_doc.page_count):
        lines = [x.strip() for x in pdf_doc[i].get_text().splitlines() if x.strip()]
        if not lines:
            continue
        if start is None:
            if len(lines) <= _THIN_PAGE and any(_RE_BOTONG.match(l) for l in lines):
                start = i
            continue
        if any(_RE_APPENDIX.match(l) or _RE_LAW_APPENDIX.match(l) for l in lines):
            end = i                       # 별표·관계법령 중 먼저 오는 쪽에서 끊는다
            break
    return range(0 if start is None else start, end)


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    if not raw.startswith(b"%PDF"):
        # 크롤러가 이미 걸렀지만, 손으로 넣은 파일이 올 수 있다
        raise RuntimeError(f"PDF 가 아니다: {raw[:60]!r}")

    # 상품명이 곧 문서 제목이다 (위 ①). 크롤러가 목록에서 읽어 meta 에 넣어 뒀다
    title = (doc.meta.get("document_title") or "").strip()
    if not title:
        raise RuntimeError(
            f"{doc.doc_id}: meta 에 document_title 이 없다. "
            "크롤러가 상품명을 못 읽었다는 뜻이라, 여기서 지어내지 않고 멈춘다.")

    with _open(raw) as pdf_doc:
        body = _body_pages(pdf_doc)
        out = pdf.elements(pdf_doc, doc.doc_id, title=title,
                           pages=body, terms_re=_RE_INSURANCE_TERMS)
        total_pages = pdf_doc.page_count

    if not out.elements:
        raise RuntimeError(f"{doc.doc_id}: 요소를 하나도 못 뽑았다 — PDF 구조가 다르다")

    articles = sum(1 for e in out.elements if e.type == "article")
    if not articles:
        # 조가 없으면 약관이 아니다. 상품요약서나 사업방법서를 받았을 수 있다
        raise RuntimeError(
            f"{doc.doc_id}: 조문이 하나도 없다 — `_file1`(약관)이 아닌 파일을 받았을 수 있다")

    counts = {"articles": articles, "body_pages": len(body), "pdf_pages": total_pages, **out.counts}
    warnings = list(out.warnings)
    if len(body) == total_pages:
        # 마커를 못 찾았다. 관계법령이 약관 조로 섞여 들어갔을 수 있으니 조용히 넘기지 않는다
        warnings.append(
            "약관 본문 경계를 못 찾아 전체를 읽었다 — 관계법령이 섞였을 수 있다 (RAG-039 ⑤)")
    return Parsed(
        elements=out.elements,
        document_title=title,
        # 판매개시일. 크롤러가 공시실 목록에서 읽어 meta 에 넣어 뒀다
        published_at=doc.meta.get("published_at"),
        citation_url=doc.meta.get("citation_url") or _FALLBACK_ROOM,
        counts=counts,
        warnings=warnings,
        extra={"firm": doc.meta.get("source"),
               "product_code": doc.doc_id.split("__")[0].split("-")[-1],
               "pages": counts.get("pages")},
    )
