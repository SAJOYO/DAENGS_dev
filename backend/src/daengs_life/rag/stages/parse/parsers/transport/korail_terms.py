"""코레일 여객운송약관 PDF 파서 — 포맷 층(`extract/pdf.py`)에 얹는 사이트 층 (RAG-018).

**이 파일이 아는 것은 코레일이라는 사이트뿐이다.** PDF 를 조·표로 펴는 일은 포맷 층이 하고,
여기는 문서 제목·출처 링크·`published_at` 처럼 **그 사이트라야 아는 것**만 채운다.
보험약관 카드도 같은 포맷 층을 쓰고 이 파일과 나란한 사이트 층을 하나 더 둔다.

⚠️ **`pdf` 그룹(PyMuPDF, AGPL)이 필요하다.** 없으면 `import` 에서 바로 실패하는 대신
무엇을 해야 하는지 알려 준다 (아래 `_open`). `ml` 그룹이 빠졌을 때 `/ask` 만 503 이 되던 것과
같은 성격의 자리인데, 그쪽은 런타임이고 여기는 오프라인 파이프라인이라 **그냥 실패하는 편이
낫다** — 조용히 0건으로 끝나면 RAG-030 ① 이 겪은 그 소실이 된다.

문서 두 벌
  `korail-terms-passenger`  여객운송약관 + 정기승차권약관 (합본). **반려동물 조항이 여기 있다** —
                            제43조 휴대금지 물품의 예외로 "필요한 예방접종을 한 반려동물을
                            전용가방 등에 넣은 경우"
  `korail-terms-gwangyeok`  광역철도 여객운송약관. 같은 취지가 "애완용동물을 용기에 넣고" 로
                            적혀 있고 **장애인 보조견 예외**가 따로 있다

출처 링크 — 첨부 URL 은 `fileNo` 가 개정마다 바뀌어 링크가 늙는다. **목록 페이지를 인용한다**
(`info.korail.com/info/contents.do?key=922`). 사람이 열면 현행 첨부가 거기 있고, 그 주소는
개정과 무관하게 유지된다 — 조례에서 `ordinSeq` 대신 이름 URL 을 고른 것과 같은 판단(RAG-033 ④).
"""
from __future__ import annotations

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.stages.parse.extract import pdf
from ..base import Parsed

NAME = "korail_pdf"
VERSION = 1

LIST_URL = "https://info.korail.com/info/contents.do?key=922"

# slug 접미사 → (문서 제목, 합본의 첫 약관 이름).
# 제목을 여기서 주는 이유 — PDF 의 `metadata.title` 이 둘 다 빈 문자열이다 (2026-08-28 실측).
DOCS = {
    "passenger": "여객운송약관",
    "gwangyeok": "광역철도여객운송약관",
}


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


def _suffix(doc_id: str) -> str:
    """`korail-terms-passenger__20260828` → `passenger`."""
    return doc_id.split("__")[0].rsplit("-", 1)[-1]


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    if not raw.startswith(b"%PDF"):
        # 크롤러가 이미 걸렀지만, 손으로 넣은 파일이 올 수 있다
        raise RuntimeError(f"PDF 가 아니다: {raw[:60]!r}")

    suffix = _suffix(doc.doc_id)
    title = DOCS.get(suffix)
    if title is None:
        raise RuntimeError(
            f"모르는 문서다: {doc.doc_id!r} (접미사 {suffix!r}).\n"
            f"  아는 것: {sorted(DOCS)} — 소스에 첨부가 늘었으면 여기도 같이 늘릴 것.")

    with _open(raw) as pdf_doc:
        out = pdf.elements(pdf_doc, doc.doc_id, title=title)

    if not out.elements:
        raise RuntimeError(f"{doc.doc_id}: 요소를 하나도 못 뽑았다 — PDF 구조가 다르다")

    articles = sum(1 for e in out.elements if e.type == "article")
    if not articles:
        # 조가 없으면 약관이 아니다. 표지만 받았거나 다른 파일을 받은 것이다
        raise RuntimeError(f"{doc.doc_id}: 조문이 하나도 없다 — 다른 문서를 받았을 수 있다")

    counts = {"articles": articles, **out.counts}
    return Parsed(
        elements=out.elements,
        document_title=title,
        # 시행일. 크롤러가 링크 라벨에서 읽어 meta 에 넣어 뒀다 (광역철도는 라벨이 없어 None)
        published_at=doc.meta.get("published_at"),
        citation_url=LIST_URL,
        counts=counts,
        warnings=out.warnings,
        extra={"file_no": doc.meta.get("file_no"), "pages": counts.get("pages")},
    )
