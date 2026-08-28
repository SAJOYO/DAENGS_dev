"""보조금24 서비스 상세 JSON 파서 — 필드 묶음이 청크 경계 (RAG-004, RAG-019).

법령·조례는 원본이 `제N조` 로 경계를 정해 줬다. **여기에는 그런 것이 없다** — 서비스 하나가
평평한 JSON 이고 조문 번호도 계층도 없다. 그래서 카드가 *"`section` 대신 필드명이 청크 경계가
된다"* 고 정해 뒀고, 이 파서는 그것을 구현하되 **필드 하나 = 청크 하나로는 하지 않는다.**

  이유 — `신청기한: 상시신청`, `지원유형: 현금(감면)` 처럼 **한 줄짜리 필드가 많다.** 그대로
  쪼개면 열 글자짜리 청크가 서비스마다 서넛씩 생기는데, 임베딩상 서로 거의 같아 검색을 흐리기만
  한다. 그래서 사람이 묻는 단위로 묶었다 (`GROUPS`): 무엇을 · 누구에게 · 어떻게 · 어디에 · 근거.
  묶음 이름이 `section` 이 되고 그것이 인용 문자열이 된다 ("… 지원내용", "… 신청방법").

**요소 타입은 `Article` 이다.** 조문이 아닌데 `article` 을 쓰는 것이 어색하지만, 대안인
`Para` 는 **청커가 easylaw 가 아닌 문서에서 조용히 버린다**(`chunk.py` 의 `para: 소제목 밖`).
새 타입을 만들거나 청커에 소스별 분기를 하나 더 다는 길도 있었는데, `Article` 은
`paragraphs=[]` 일 때 정확히 "제목 + 본문 한 덩어리 = 청크 하나" 로 동작해서
(`chunk._article` 의 첫 분기) 조례가 이미 검증한 경로를 그대로 탄다. 공유 코드를 안 건드리는
쪽을 골랐다 — 나중에 JSON 소스가 여럿이 되면 그때 타입을 만드는 편이 근거가 분명하다.

원본의 성질 (2026-08-28 실측)
  - 여러 값이 `||` 로 이어 온다: `유아교육법(제24조)||영유아보육법(제34조)`,
    `교육부/02-6222-6060||0079에듀콜/1544-0079-5-1`. ` / ` 로 편다
  - 없는 필드는 **`None`** 이다 (빈 문자열이 아니다). `자치법규`·`법령` 이 특히 자주 비어 있다
  - `수정일시` 가 상세는 `2026-01-29`, 목록은 `20260129201825` 로 형식이 다르다.
    **이 값이 `published_at` 이다** — 지원사업은 해마다 바뀌어서 답변에 기준일이 실려야
    사용자가 낡은 정보를 알아챈다 (카드 #39 메모 ⑤)
  - 본문에 `\\r\\n` 과 전각 공백이 섞여 온다

출처 링크 — `https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{서비스ID}` 다. 목록조회의
`상세조회URL` 과 **표본 6/6 이 일치**해서 서비스ID 하나로 유도한다. API 주소는 키가 `***` 로
가려져 사람이 못 열고(RAG-012), 목록 행은 raw 에 저장하지 않으므로 유도가 유일한 길이다.
"""
from __future__ import annotations

import re

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Article
from ..base import Parsed

NAME = "benefit24_json"
VERSION = 1

GOV24 = "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/"

# (묶음 이름, 그 묶음에 들어갈 원본 필드). 순서가 문서 순서가 된다.
# 묶음 이름은 **사람이 답변에서 볼 인용 문자열**이라 원본 필드명을 그대로 쓴다.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("서비스목적", ("서비스목적",)),
    ("지원내용", ("지원내용", "지원유형")),
    ("지원대상", ("지원대상", "선정기준")),
    ("신청방법", ("신청방법", "신청기한", "구비서류", "공무원확인구비서류",
                  "본인확인필요구비서류", "온라인신청사이트URL")),
    ("문의처", ("접수기관명", "문의처")),
    # 근거는 따로 둔다 — 이 값이 코퍼스 안의 조례·법령 문서와 같은 문자열이라,
    # 나중에 문서 간 링크를 걸 때 이 청크가 그 접점이 된다
    ("근거법령", ("법령", "자치법규")),
)

_WS = re.compile(r"[\s　]+")


def _clean(v: object) -> str:
    """`||` 를 펴고 공백을 고른다. `None`·빈 값은 빈 문자열."""
    if v is None:
        return ""
    s = str(v).strip()
    if not s or s.lower() == "none":
        return ""
    return _WS.sub(" ", s.replace("||", " / ")).strip()


def _ymd(raw: object) -> str | None:
    """`2026-01-29` 또는 `20260129201825` → `2026-01-29`."""
    d = re.sub(r"\D", "", str(raw or ""))
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else None


def _title(name: str, org: str) -> str:
    """**소관기관명을 제목 앞에 붙인다.** 사업명에 지자체가 안 들어 있는 경우가 많다.

    실측 (2026-08-28 검문소③) — `동대문구 취약계층 반려동물 의료비 얼마 지원하나요` 의 1위가
    **충청북도** 사업이었다. 동대문구 것의 서비스명이 그냥 `취약계층 반려동물 의료비 지원` 이라
    본문 어디에도 "동대문구" 가 없었고, 충청북도 것은 사업명 자체가
    `충청북도 취약계층 반려동물 의료비 지원` 이라 이겼다.

    청크 본문이 `{document_title} {head}` 로 조립되므로(`chunk._article`) 제목에 넣는 것만으로
    지자체명이 임베딩에 들어간다. RAG-033 ⑥ 이 남긴 "지자체 이름을 dense 가 못 가른다" 의
    **일부**가 이것으로 풀린다 — 조례는 이름이 원래 제목에 있어서 이 처방이 안 듣는다.
    거기는 여전히 하이브리드/BM25 쪽 결정이 필요하다.

    이미 들어 있으면 겹쳐 쓰지 않는다 (`충청북도 충청북도 …` 를 만들지 않는다).
    """
    if not org or org in name:
        return name
    return f"{org} {name}"


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    import json

    body = json.loads(raw)
    rows = body.get("data") or []
    if not rows:
        raise RuntimeError("data 가 비었다 — 응답 규격이 다르거나 서비스가 사라졌다")
    row = rows[0]

    sid = _clean(row.get("서비스ID"))
    if not sid:
        raise RuntimeError("서비스ID 가 없다 — 응답 규격이 다르다")

    title = _title(_clean(row.get("서비스명")) or doc.meta.get("document_title", ""),
                   _clean(row.get("소관기관명")))
    doc_id = doc.doc_id
    elements: list = []
    warnings: list[str] = []

    for section, fields in GROUPS:
        # 원본 필드명을 값 앞에 붙인다. 묶음 안에 필드가 둘 이상이면 어느 줄이 무엇인지
        # 사라지기 때문이고, 필드가 하나뿐일 때도 붙여야 청크 사이의 모양이 같다
        lines = [f"{f}: {v}" for f in fields if (v := _clean(row.get(f)))]
        if not lines:
            continue
        head = "\n".join(lines)
        elements.append(Article(
            id=f"{doc_id}#{section}", section=section, title=section,
            head=head, paragraphs=[], chars=len(head)))

    if not elements:
        raise RuntimeError(f"서비스 {sid} 에서 본문 필드를 하나도 못 뽑았다 — 규격 확인 필요")

    # 지원사업의 핵심은 "무엇을 얼마나" 다. 그것이 비어 있으면 문서가 껍데기라 알려 준다
    if not any(e.section == "지원내용" for e in elements):
        warnings.append("지원내용이 비었다 — 이 서비스는 안내만 있고 지원 내역이 없다")

    return Parsed(
        elements=elements,
        document_title=title,
        published_at=_ymd(row.get("수정일시")) or doc.meta.get("published_at"),
        citation_url=GOV24 + sid,
        counts={"fields": len(elements)},
        warnings=warnings,
        extra={"service_id": sid,
               "org": _clean(row.get("소관기관명")),
               "support_type": _clean(row.get("지원유형")),
               "deadline": _clean(row.get("신청기한")),
               "law": _clean(row.get("법령")),
               "ordinance": _clean(row.get("자치법규"))},
    )
