"""손해보험협회 반려동물보험 공시 파서 — 상품이 문서 안의 마디, 담보가 표의 행.

원본 한 파일 = **회사 하나**이고 그 안에 상품 여러 개가 평평한 행으로 들어 있다
(216행 = 상품 48 × 담보). 행을 그대로 요소로 만들면 같은 상품의 개요가 담보 수만큼
반복되므로, **상품(`TP_CODE`)으로 되접어** 상품마다 요소 둘을 만든다.

  Article  `{상품명}` — 개요. 회사·채널·예시보험료·**특이사항**(`TP_ETC`)
  Table    `{상품명} 보장내용` — 담보명 · 지급사유 · 지급액

**왜 둘로 나누는가.** 이 소스에 오는 질문은 두 종류다. "자기부담금 얼마" · "가입 나이 제한"
같은 **가입 조건**은 전부 `TP_ETC` 한 필드에 있고(중앙값 483자), "슬개골 보장되나" 같은
**보장 여부**는 담보 목록에 있다. 한 덩어리로 합치면 상품마다 1,000자가 넘어가 청커가 쪼개는데,
그 쪼갬은 우리가 의미로 자른 것이 아니라 길이로 잘린 것이다. 미리 두 축으로 나눠 두면
각 청크가 한 질문에 대응한다.

**보장내용을 `Table` 로 두는 것이 요점이다.** 담보는 `담보명 | 지급사유 | 지급액` 세 칸이
짝을 이뤄야 뜻이 된다 — `1억원` 이 무엇에 대한 1억원인지는 옆 칸에만 있다. 청커의 표 규칙이
이미 그것을 처리한다 (RAG-004 ③(나)): 1,000자 이하면 통짜, 넘으면 **`헤더: 값`** 으로 행마다
푼다. 실측 보장표 크기는 중앙값 468자·최대 1,668자라 **대부분 통짜, 큰 것만 행 단위**로 갈린다.
`chunk.py` 는 건드리지 않는다 (RAG-031 ④).

개요를 `Article` 로 두는 이유는 benefit24 파서와 같다 — `Para` 는 청커가 easylaw 밖에서
조용히 버리고, `Article` 은 `paragraphs=[]` 일 때 "제목 + 본문 = 청크 하나" 로 정확히 동작한다.

원본의 성질 (2026-08-28 실측)
  - 상품 수준 21필드는 그 상품의 모든 행에 **같은 값으로 반복**된다. 담보 수준은 다섯뿐이다
    (`TP_PAY_NAME` `TP_PAY_REASON` `TP_PAY` `NUM1` `ROW_NUM`)
  - 빈 값이 `''` 가 아니라 **`'-'`** 로 온다 (`MIN_BILL`, `ADD_INSRNC_IDX`, `CNTRCT_CNCLS_IDX`)
  - `TP_ETC` 안에 빈 줄이 섞여 있고 `ㆍ` 로 항목을 나눈다. 줄바꿈을 살려야 항목이 붙지 않는다
  - 보험료(`TP_W_BILL`/`TP_M_BILL`)는 **숫자**로 온다. 그대로 두면 `34365` 라 읽기 어렵다
  - ⚠️ **상품명이 유일하지 않다.** 현대해상 `(무)현대해상다이렉트굿앤굿우리펫보험(Hi2601) 2종`
    하나가 `TP_CODE` 둘(`…4264`·`…4265`)로 온다. 요소 id 를 상품명으로 만들면 두 상품이
    같은 id 가 되어 뒤엣것이 앞엣것을 덮는다 — **id 는 `TP_CODE` 로 짓는다**

`published_at` 은 없다. 공시 기준일자가 원본 어디에도 없다 (소스 모듈 정찰 참고).
"""
from __future__ import annotations

from daengs_life.crawler.core import jsobject      # 포맷 지식 — 한 벌만 둔다 (textutil 과 같은 이유)
from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Article, Table
from ..base import Parsed

NAME = "knia_disclosure_json"
VERSION = 1

# 공시실 딥링크. 회사마다 다르고 원본이 행마다 들고 있다 (`TP_URL`)
_FALLBACK_URL = "https://kpub.knia.or.kr/productDisc/longTermGuarantee/petCareInsurance.do"

# 개요에 실을 상품 수준 필드 — (표시 이름, 원본 키). 순서가 그대로 본문 순서다
OVERVIEW: tuple[tuple[str, str], ...] = (
    ("보험회사", "P_CODE_NM"),
    ("판매채널", "TP_NEW_CHANNEL"),
    ("대표번호", "ETC5"),
)

# 표의 헤더. 청커가 `헤더: 값` 으로 풀 때 이 이름이 그대로 붙는다
HEADER = ["담보명", "지급사유", "지급액"]


def _clean(v: object) -> str:
    """`'-'`·`None`·빈 값은 빈 문자열로. 줄바꿈은 살리고 그 밖의 공백만 고른다."""
    if v is None:
        return ""
    s = str(v).strip()
    if s in ("", "-", "None"):
        return ""
    lines = [" ".join(line.split()) for line in s.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _won(v: object) -> str:
    """`34365` → `34,365원`. 숫자가 아니면 그대로 둔다."""
    s = _clean(v)
    if not s:
        return ""
    try:
        return f"{int(float(s)):,}원"
    except ValueError:
        return s


def _rows(raw: bytes) -> list[dict]:
    """응답 → 행 목록.

    **소스 모듈을 import 하지 않는다.** `rag` 가 `crawler.sources.*` 에 닿으면 파이프라인의
    한 방향(RAG-014)이 끊긴다 — 허용된 것은 `crawler.core` 의 포맷·경로 지식뿐이다
    (`test_import_direction_packages`). `list` 키 세 줄은 benefit24 파서가 `data` 키를
    소스와 따로 꺼내는 것과 같은 종류의 중복이다.
    """
    data = jsobject.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise RuntimeError(f"응답에 list 배열이 없다 — 규격 확인 필요: {str(data)[:200]}")
    return data["list"]


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    rows = _rows(raw)
    if not rows:
        raise RuntimeError("list 가 비었다 — 응답 규격이 다르거나 판매 상품이 사라졌다")

    firm = _clean(rows[0].get("P_CODE_NM"))
    if not firm:
        raise RuntimeError("P_CODE_NM 이 없다 — 응답 규격이 다르다")

    # **상품 키는 TP_CODE 다** (상품명은 겹칠 수 있다 — 위 정찰). dict 가 등장 순서를 지킨다
    products: dict[str, list[dict]] = {}
    for row in rows:
        code = _clean(row.get("TP_CODE"))
        if not code:
            raise RuntimeError(f"행에 TP_CODE 가 없다 — 규격 확인 필요: {row}")
        products.setdefault(code, []).append(row)

    doc_id = doc.doc_id
    elements: list = []
    warnings: list[str] = []
    citation_url = ""

    for code, group in products.items():
        first = group[0]
        name = _clean(first.get("TP_NAME")) or code
        citation_url = citation_url or _clean(first.get("TP_URL"))

        # ---------------------------------------------------- 개요 (Article)
        lines = [f"{label}: {v}" for label, key in OVERVIEW if (v := _clean(first.get(key)))]
        weekly, monthly = _won(first.get("TP_W_BILL")), _won(first.get("TP_M_BILL"))
        if weekly or monthly:
            # 협회 공시의 보험료는 '예시'다. 조건이 `TP_ETC` 에 붙어 있어서 그 말을 빼면
            # 누구에게나 그 값인 것처럼 읽힌다
            lines.append(f"예시보험료: 여성 {weekly or '-'} / 남성 {monthly or '-'}"
                         " (아래 특이사항의 기준계약 조건 기준)")
        if etc := _clean(first.get("TP_ETC")):
            lines.append(f"특이사항: {etc}")

        head = f"{name}\n" + "\n".join(lines)
        elements.append(Article(
            id=f"{doc_id}#{code}",
            section=name,
            title=name,
            head=head,
            paragraphs=[],
            chars=len(head),
            citation_url=_clean(first.get("TP_URL")) or None,
        ))

        # ---------------------------------------------------- 보장내용 (Table)
        table_rows = [
            [_clean(r.get("TP_PAY_NAME")), _clean(r.get("TP_PAY_REASON")), _clean(r.get("TP_PAY"))]
            for r in group
        ]
        table_rows = [r for r in table_rows if any(r)]
        if not table_rows:
            warnings.append(f"{name}: 담보가 하나도 없다")
            continue
        # `title` 에 상품명을 넣지 않는다 — 청커의 표 캡션이 `문서제목 + section + title` 이라
        # (`_table_caption`) section 에 이미 상품명이 있으면 캡션에 두 번 들어간다
        elements.append(Table(
            id=f"{doc_id}#{code}-보장내용",
            title="보장내용",
            section=name,
            header=list(HEADER),
            rows=table_rows,
            citation_url=_clean(first.get("TP_URL")) or None,
        ))

    if not elements:
        raise RuntimeError(f"{firm} 에서 상품을 하나도 못 뽑았다 — 규격 확인 필요")

    return Parsed(
        elements=elements,
        document_title=f"{firm} 반려동물보험 공시",
        published_at=None,                       # 원본에 공시 기준일자가 없다
        citation_url=citation_url or _FALLBACK_URL,
        counts={"products": len(products), "coverages": len(rows)},
        warnings=warnings,
        extra={"firm": firm, "p_code": _clean(rows[0].get("P_CODE"))},
    )
