"""보험사 상품공시실 약관 PDF — **삼성화재부터.**

`knia-disclosure`(협회 비교공시, RAG-038)가 담보·지급사유·자기부담금을 덮었지만
**보장한도·면책조항·가입 나이 상한처럼 약관에만 있는 것**은 아직 답이 안 된다. 이 소스가
그 층이다. 조항 번호(`제○조`)가 그대로 인용 지표가 되므로 법령과 같은 방식으로 저장된다.

────────────────────────────────────────────────────────────────────────────
정찰 (2026-08-28) — 실측
────────────────────────────────────────────────────────────────────────────
**공시실이 Vue SPA 라 초기 HTML 에는 상품이 하나도 없다.** 페이지가 아니라 그 페이지가 쓰는
스크립트(`/sfmi/v2/ui/info/IH_IF_Terms.js`)에 목록 엔드포인트가 적혀 있었다:

    /vh/sfmi/ui/wshomepage/search/search_law_xml.jsp?kwd={검색어}&sMethod=and

JS 는 `POST` 로 부르지만 **`GET` 도 같은 XML 을 준다**(실측). `Fetcher` 가 GET 만 하므로
이것이 중요하다 — POST 만 받았으면 core 를 고쳐야 했다.

응답 XML 은 두 묶음이다. **`SearchNlawResult` = 판매중, `SearchYlawResult` = 판매종료**
(펫 계열 기준 11건 대 85건). **판매중만 받는다** — 지금 가입할 수 있는 상품의 약관이
사용자 질문의 대상이고, 종료분 85건까지 넣으면 같은 상품의 옛 개정판이 코퍼스에서 서로
경쟁한다.

각 item 이 **PDF 경로를 직접 준다.** 파일명을 조립할 필요가 없다:

    n_policy   약관        ← 이 소스가 받는 것
    n_method   사업방법서   (안 받는다 — 판매 절차 문서라 사용자 질문과 멀다)
    n_summary  상품요약서   (안 받는다 — 약관의 발췌라 내용이 겹친다)

⚠️ **`kwd` 하나로는 다 안 걸린다.** `펫` 은 `반려묘보험`(다이렉트 반려묘보험 2종)을 놓치고
`반려묘` 는 `착한펫보험` 을 놓친다. 네 키워드의 **합집합**을 쓴다. 상품명 검색이라
benefit24 가 겪은 `반려`=返戾 동음이의 문제는 없다 (그쪽은 지원내용 본문을 훑었다).

⚠️ **협회 공시(6건)보다 회사 공시실(11건)이 넓다.** knia PB27 에 안 올린 상품이 있다
(`반려견보험 애니펫`·`반려묘보험 애니펫`·`반려견 상해보험`). 회사 쪽이 원본이므로 이쪽을 따른다.

⚠️ **시드에 적혀 있던 삼성 PDF 는 옛 판이다.** `ZPB316050_0_20240401`(위풍댕댕, 11.8MB·202p)은
아직 열리지만 현행은 `ZPB316090_0_20260701` 이다. **상품코드까지 바뀌었다** — URL 을 시드에
고정하면 개정 후에도 옛 판을 계속 받는다. 그래서 매번 목록에서 읽는다 (`_lawgokr` 의 lsiSeq 와
같은 판단).

실측 (판매중 11건 전수)
  **25.1MB · 1,530페이지 · 표 494개.** 0자 페이지 18/1530 = **1.2%** — 전부 간지이고
  **스캔 PDF 는 0건**이다. RAG-032 ③ 의 "표본 4개에서 0건"이 11건에서도 유지된다.
  옛 판 하나가 11.8MB 였던 것과 달리 현행은 1.8~3.2MB 라, 용량 걱정(카드 메모 ③)은
  생각보다 작다.

**회사를 늘리는 자리** — `_ADAPTERS` 에 함수를 하나 더한다. 나머지 6사는 공시실 구조가
사별로 달라(SPA 2 · robots 차단 1 · AJAX 3) 각자 역추적이 필요하고, 그 판단은 카드의
검문소 ⓐ 에서 사람이 한다.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote

from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

SAMSUNG = "https://www.samsungfire.com"
SAMSUNG_SEARCH = f"{SAMSUNG}/vh/sfmi/ui/wshomepage/search/search_law_xml.jsp"
# 사람이 열어 볼 수 있는 공시실 주소. API 응답에는 없어서 답변 링크로 이것을 쓴다
SAMSUNG_ROOM = f"{SAMSUNG}/vh/page/VH.REIF0011.do"

# 하나로는 다 안 걸린다 (위 정찰). 합집합을 쓴다
KEYWORDS = ("펫", "반려", "반려묘", "반려견")

# 이보다 적게 걸리면 검색 규격이나 판매 상품이 바뀐 것이다. 실측 11건
MIN_PRODUCTS = 5


def _samsung(fetcher: Fetcher) -> list[dict]:
    """삼성화재 공시실 → 판매중 펫 상품. 상품명이 키라 중복이 자동으로 합쳐진다."""
    found: dict[str, dict] = {}
    for kwd in KEYWORDS:
        url = f"{SAMSUNG_SEARCH}?kwd={quote(kwd)}&sMethod=and"
        res = fetcher.get(url)
        if not res.ok:
            raise RuntimeError(f"삼성 목록 조회 실패: HTTP {res.status} {url}")
        root = ET.fromstring(res.content)
        section = root.find("SearchNlawResult")          # 판매중만. 종료분(Ylaw)은 안 받는다
        if section is None:
            raise RuntimeError(
                f"응답에 SearchNlawResult 가 없다 — 규격 확인 필요: {res.content[:200]!r}")
        for item in section.findall("item"):
            row = {c.tag: (c.text or "").strip() for c in item}
            name, policy = row.get("n_productName"), row.get("n_policy")
            if not name or not policy:
                continue                                  # 약관 파일이 없는 상품은 건너뛴다
            found[name] = {
                "name": name,
                "url": SAMSUNG + policy,
                # `/publication/pdf/ZPB316090_0_20260701_file1.pdf` → `ZPB316090_0_20260701`
                "code": policy.rsplit("/", 1)[-1].removesuffix("_file1.pdf"),
                "sell_from": row.get("n_productDay", "").replace(".", "-"),
                "category": row.get("n_productCategory", ""),
                "room": SAMSUNG_ROOM,
                "firm": "삼성화재",
            }
    return sorted(found.values(), key=lambda p: p["code"])


# 회사 하나 = 함수 하나. 늘릴 때 여기에 더한다 (나머지 6사는 카드의 검문소 ⓐ 뒤)
_ADAPTERS = {"samsung": _samsung}


class InsurerTermsPdfs(Source):
    id = "insurer-terms-pdfs"
    domain = "insurance"
    category = "policy"
    subcategory = "insurance"
    source_type = "document"                 # 웹페이지가 아니라 문서 파일이다
    format = "pdf"
    trust_level = "official"
    # ⚠️ 공공누리가 아니다 — 보험사 저작물이다. 원본은 `data/` 에만 두고(RAG-017),
    # 서비스 표출 시 출처 표기가 필수이며 인용 범위를 넘지 않게 해야 한다 (data-sources §12)
    license = "보험사 저작물 — 인용 시 출처 표기"

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        products: list[dict] = []
        for firm_key, adapter in _ADAPTERS.items():
            got = adapter(fetcher)
            if not got:
                raise RuntimeError(f"{firm_key}: 판매중 상품을 하나도 못 찾았다 — 규격 확인 필요")
            products += [dict(p, firm_key=firm_key) for p in got]

        if len(products) < MIN_PRODUCTS:
            raise RuntimeError(
                f"판매중 약관이 {len(products)}건뿐이다 (실측 11건). "
                f"키워드 {' '.join(KEYWORDS)} 로 걸리던 조건이라, 적으면 검색 규격이 바뀐 것이다.")

        return [
            Target(
                url=p["url"],
                slug=f"{self.id}-{p['firm_key']}-{p['code']}",
                ext="pdf",
                meta={
                    "title": p["name"],
                    "firm": p["firm"],
                    "product_code": p["code"],
                    # 판매개시일이다. 약관의 시행일과 같은 값으로 쓰되, 파서가 표지에서
                    # 더 정확한 것을 읽으면 그쪽이 이긴다
                    "published_at": p["sell_from"] or None,
                    "citation_url": p["room"],
                    "notes": f"{p['firm']} 상품공시실 약관 — {p['name']}",
                },
            )
            for p in products
        ]

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        """PDF 는 여기서 열지 않는다.

        `format='pdf'` 라 변경 감지 지문이 **원본 바이트**로 잡히고(`store.save`), 본문 해석은
        파싱 단계의 `extract/pdf.py` 몫이다. 여기서 `pymupdf` 를 부르면 **크롤러가 `pdf` 그룹에
        의존**하게 되는데, 그 그룹은 AGPL 격리 때문에 backend 컨테이너에 없다 (RAG-032 ②).
        그러면 수집이 컨테이너에서 안 돈다.

        대신 **PDF 인지만 확인한다.** 로그인 페이지나 오류 HTML 이 200 으로 오는 경우가 있고,
        그것을 그대로 저장하면 파싱 단계에서야 드러난다.
        """
        if res.content[:5] != b"%PDF-":
            raise RuntimeError(
                f"{target.meta.get('title')}: PDF 가 아니다 "
                f"(status={res.status}, ct={res.content_type}, 앞 20바이트={res.content[:20]!r})")

        return Extracted(
            title=target.meta.get("title", ""),
            text="",                                   # 지문은 바이트로 잡힌다
            published_at=target.meta.get("published_at"),
            extra={"firm": target.meta.get("firm"),
                   "product_code": target.meta.get("product_code"),
                   "bytes": len(res.content)},
        )
