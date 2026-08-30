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

────────────────────────────────────────────────────────────────────────────
KB손해보험 (2026-08-30, #58 · RAG-048)
────────────────────────────────────────────────────────────────────────────
목록(`/CG802030001.ec`)은 POST 폼이지만 **같은 이름의 쿼리로 GET 도 받는다**(실측). 폼의
hidden 필드(`goodsNm` · `onsaleYn`)가 아니라 **화면 입력 필드 이름**(`search_goods_nm` ·
`search_onsale_yn`)이 실제 조건이다 — hidden 만 넣으면 조건 없이 전체가 온다. 페이지는
euc-kr 이라 검색어도 euc-kr 로 인코딩해야 한다. 10건씩 넘기고(`devonTargetRow` = 1, 11, 21 …)
행은 `detail('보종','종류','차수')` 로 상세를 가리킨다.

⚠️ **같은 상품이 보험종류(gubun)마다 보종 번호를 따로 받는다.** `KB 금쪽같은 펫보험(강아지)`
은 상해보험(25343)과 제휴(25344)에 한 번씩 있고 PDF 는 표지의 보종 번호만 다르다(195쪽 중
2쪽 diff). 삼성과 같이 **상품명이 키**다 — 첫 것을 받고 나머지는 합친다.

상세(`/CG802030002.ec?bojongNo&gubun&bojongSeq`)는 **판매 이력 표**다. 판매종료일이 빈 행이
현행이고 그 행의 `보험약관` 링크(`/CG802030003.ec?fileNm=…`)를 받는다. 파일명 규칙이 판마다
다르다 — 신형 `25343_1_1.pdf` · 구형 `20260101_17390_1.pdf` — 그래서 **조립하지 않고 링크를
읽는다.** 끝의 `_1` 이 약관이고 `_2` 사업방법서 · `_3` 상품요약서다.

⚠️ **약관 자리에 다른 파일이 걸린 상품이 있다.** `KB 다이렉트 금쪽같은 펫보험(고양이)(재가입용)`
(25200)의 약관 링크가 `25200_1_3.pdf`(8쪽 "상품별 특이사항")다 — 공시실 쪽 오류다. `_1.pdf`
로 끝나지 않는 약관 링크는 **받지 않고 건너뛴 사실을 찍는다.** 받으면 파서가 "조문이 없다"로
멈추는데, 그건 매 파싱마다 되풀이되는 실패라 수집 단계에서 거른다.

`gubun` `i`(독립특별약관)·`j`(제도성 특별약관)는 상품이 아니라 특약 낱장이라 뺀다.

────────────────────────────────────────────────────────────────────────────
NH농협손해보험 (2026-08-30, #58 · RAG-048)
────────────────────────────────────────────────────────────────────────────
공시실(`retrieveInsuranceProductsAnnounce.nhfire`)이 ajax 세 단이다. 화면 JS 가 부르는 순서 그대로
GET 이 된다 (`;jsessionid=…` 없이도).

    /front/announce/retrievePdtCd.ajax?type=ajax&pdtSelYn=Y&pdtGrCd={01|02}&pdtDcd=   → 상품 목록
    /front/announce/retrievePdtInfo.ajax?type=ajax&fileType=05&pdtCd={pdtCd}             → 판매 이력 + 파일
    /imageView/downloadFile.ajax?fileId={fileId}&afileSeqn={plcndAfileSeqn}              → PDF

`pdtGrCd` 01=장기 · 02=일반 (03 자동차 · 04 농작물재해는 뺀다). 상품군을 비우면 빈 응답이 온다.
응답은 `<xsync><LMultiData>` 밑에 **열 단위**로 반복된다(`pdtCd` 가 n개, 그 뒤 `pdtNm` 이 n개 …)
— 행이 아니라 열이라 태그별로 모아 인덱스로 묶는다.

이력 행은 `pdtSelEdDt` 가 `99991231`(또는 `29991231`·빈 값)인 것이 현행이다. 파일 열은
`plcnd`(약관) · `smmr`(요약서) · `ntclt`(안내장) · `bzMtd`(사업방법서) — 약관만 받는다.

⚠️ **약관이 합본이다.** `NH다이렉트펫앤미든든보험` 1종(강아지)·2종(고양이)·3종(재가입용)이
**같은 PDF 한 벌**(358쪽)을 가리킨다 — `fileId` 는 상품마다 다른데 파일명·바이트가 같다.
파일명이 키다. 제목은 상품명에서 `[N종:…]` 을 뗀 공통 부분으로 쓴다
(`(무) NH다이렉트펫앤미든든보험[1종:강아지]2604` → `무배당 NH다이렉트펫앤미든든보험2604`,
표지와 같은 표기).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from bs4 import BeautifulSoup

from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

SAMSUNG = "https://www.samsungfire.com"
SAMSUNG_SEARCH = f"{SAMSUNG}/vh/sfmi/ui/wshomepage/search/search_law_xml.jsp"
# 사람이 열어 볼 수 있는 공시실 주소. API 응답에는 없어서 답변 링크로 이것을 쓴다
SAMSUNG_ROOM = f"{SAMSUNG}/vh/page/VH.REIF0011.do"

# 하나로는 다 안 걸린다 (위 정찰). 합집합을 쓴다
KEYWORDS = ("펫", "반려", "반려묘", "반려견")

# 이보다 적게 걸리면 검색 규격이나 판매 상품이 바뀐 것이다. 실측 삼성 11 · KB 10 · 농협 3
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


# ---------------------------------------------------------------------------- KB
KB = "https://www.kbinsure.co.kr"
KB_LIST = f"{KB}/CG802030001.ec"
KB_DETAIL = f"{KB}/CG802030002.ec"
KB_ROOM = KB_LIST                                    # 사람이 여는 공시실 = 목록 그 자체
KB_KEYWORDS = ("펫", "반려")                          # `펫` 은 `반려동물보험` 을 놓친다
KB_PAGE = 10                                         # 목록 한 쪽의 행 수 (devonTargetRow 간격)
_KB_ROW = re.compile(r"detail\('([^']*)','([^']*)','([^']*)'\);\">([^<]+)<")
_KB_SKIP_GUBUN = {"i", "j"}                          # 독립특별약관 · 제도성 특별약관 — 상품이 아니다


def _kb_list(fetcher: Fetcher, kwd: str) -> list[tuple[str, str, str, str]]:
    """검색어 하나의 판매중 상품 전부 (보종, 종류, 차수, 상품명). 10건씩 끝까지 넘긴다."""
    rows: list[tuple[str, str, str, str]] = []
    q = quote(kwd, encoding="euc-kr")
    row = 1
    while True:
        url = f"{KB_LIST}?search_goods_nm={q}&search_onsale_yn=Y&devonTargetRow={row}"
        res = fetcher.get(url)
        if not res.ok:
            raise RuntimeError(f"KB 목록 조회 실패: HTTP {res.status} {url}")
        page = _KB_ROW.findall(res.content.decode("euc-kr", errors="replace"))
        rows += [(b.strip(), g.strip(), n.strip(), name.strip()) for b, g, n, name in page]
        if len(page) < KB_PAGE:
            return rows
        row += KB_PAGE


def _kb_current_terms(fetcher: Fetcher, bojong: str, gubun: str, seq: str) -> tuple[str, str] | None:
    """상세의 판매 이력에서 **현행 행**(판매종료일 없음)의 약관 링크와 판매시작일.
    현행 행이 없거나 그 행에 약관 링크가 없으면 None — 목록엔 '판매중'이라도 약관이 없는 상품이 있다."""
    res = fetcher.get(f"{KB_DETAIL}?bojongNo={quote(bojong)}&gubun={gubun}&bojongSeq={seq}")
    if not res.ok:
        raise RuntimeError(f"KB 상세 조회 실패: HTTP {res.status} 보종 {bojong}")
    soup = BeautifulSoup(res.content.decode("euc-kr", errors="replace"), "html.parser")
    for tr in soup.select("table.tb_default04 tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        start, end = tds[0].get_text(strip=True), tds[1].get_text(strip=True)
        if end:                                       # 판매가 끝난 판
            continue
        a = tds[2].find("a", href=True)
        if a is None:
            return None
        return a["href"], start
    return None


def _kb(fetcher: Fetcher) -> list[dict]:
    """KB손해보험 공시실 → 판매중 펫 상품. 상품명이 키다 (보험종류마다 보종이 따로 있다)."""
    found: dict[str, dict] = {}
    for kwd in KB_KEYWORDS:
        for bojong, gubun, seq, name in _kb_list(fetcher, kwd):
            if gubun in _KB_SKIP_GUBUN or name in found:
                continue
            got = _kb_current_terms(fetcher, bojong, gubun, seq)
            if got is None:
                print(f"  [insurer-terms-pdfs/kb] 현행 약관 없음 — 건너뜀: {name} ({bojong})")
                continue
            href, start = got
            file_name = href.rsplit("fileNm=", 1)[-1]
            if not file_name.endswith("_1.pdf"):
                # 약관 자리에 다른 파일이 걸린 상품 (25200 → `_1_3.pdf`, 8쪽 특이사항). 위 정찰
                print(f"  [insurer-terms-pdfs/kb] 약관 링크가 약관이 아님 — 건너뜀: {name} ({file_name})")
                continue
            found[name] = {
                "name": name,
                "url": KB + href if href.startswith("/") else href,
                "code": file_name.removesuffix(".pdf"),          # 25343_1_1 · 20260101_17390_1
                "sell_from": f"{start[:4]}-{start[4:6]}-{start[6:]}" if len(start) == 8 else "",
                "category": gubun,
                "room": KB_ROOM,
                "firm": "KB손해보험",
            }
    return sorted(found.values(), key=lambda p: p["code"])


# ---------------------------------------------------------------------------- NH
NH = "https://www.nhfire.co.kr"
NH_PRODUCTS = f"{NH}/front/announce/retrievePdtCd.ajax"
NH_INFO = f"{NH}/front/announce/retrievePdtInfo.ajax"
NH_FILE = f"{NH}/imageView/downloadFile.ajax"
NH_ROOM = f"{NH}/announce/productAnnounce/retrieveInsuranceProductsAnnounce.nhfire"
NH_GROUPS = ("01", "02")                             # 장기 · 일반. 자동차(03)·농작물(04)은 뺀다
_NH_PET = re.compile(r"펫|반려")
_NH_CURRENT = {"", "99991231", "29991231"}
_NH_KIND = re.compile(r"\[[^\]]*\]")                 # `[1종:강아지]` — 합본의 종 구분


def _nh_columns(fetcher: Fetcher, url: str) -> list[dict[str, str]]:
    """`<xsync><LMultiData>` 응답을 행으로. 태그가 열 단위로 반복되므로 태그별로 모아 인덱스로 묶는다."""
    res = fetcher.get(url)
    if not res.ok:
        raise RuntimeError(f"농협 조회 실패: HTTP {res.status} {url}")
    root = ET.fromstring(res.content)
    data = root.find("LMultiData")
    if data is None:
        raise RuntimeError(f"응답에 LMultiData 가 없다 — 규격 확인 필요: {res.content[:200]!r}")
    cols: dict[str, list[str]] = {}
    for el in data:
        cols.setdefault(el.tag, []).append((el.text or "").strip())
    n = max((len(v) for v in cols.values()), default=0)
    return [{k: v[i] for k, v in cols.items() if len(v) == n} for i in range(n)]


def _nh_title(pdt_nm: str) -> str:
    """`(무) NH다이렉트펫앤미든든보험[1종:강아지]2604` → `무배당 NH다이렉트펫앤미든든보험2604` (표지 표기)."""
    name = _NH_KIND.sub("", pdt_nm).strip()
    return re.sub(r"^\(무\)\s*", "무배당 ", name)


def _nh(fetcher: Fetcher) -> list[dict]:
    """농협손해보험 공시실 → 판매중 펫 상품. **약관 파일명이 키다** (합본을 여러 상품이 가리킨다)."""
    found: dict[str, dict] = {}
    for group in NH_GROUPS:
        products = _nh_columns(
            fetcher, f"{NH_PRODUCTS}?type=ajax&pdtSelYn=Y&pdtGrCd={group}&pdtDcd=")
        for p in products:
            name = p.get("pdtNm", "")
            if not _NH_PET.search(name):
                continue
            rows = _nh_columns(fetcher, f"{NH_INFO}?type=ajax&fileType=05&pdtCd={p['pdtCd']}")
            current = [r for r in rows if r.get("pdtSelEdDt", "") in _NH_CURRENT]
            if not current:
                print(f"  [insurer-terms-pdfs/nh] 현행 판 없음 — 건너뜀: {name}")
                continue
            r = current[0]
            file_name, seq = r.get("plcndAfileNm", ""), r.get("plcndAfileSeqn", "")
            if not file_name or not seq:
                print(f"  [insurer-terms-pdfs/nh] 약관 파일 없음 — 건너뜀: {name}")
                continue
            if file_name in found:                    # 합본 — 다른 종이 같은 파일을 가리킨다
                continue
            start = r.get("pdtSelStDt", "")
            found[file_name] = {
                "name": _nh_title(name),
                "url": f"{NH_FILE}?fileId={r['fileId']}&afileSeqn={seq}",
                "code": r["fileId"],                              # F004290456 — 개정마다 바뀐다
                "sell_from": f"{start[:4]}-{start[4:6]}-{start[6:]}" if len(start) == 8 else "",
                "category": p.get("pdtDcd", ""),
                "room": NH_ROOM,
                "firm": "NH농협손해보험",
            }
    return sorted(found.values(), key=lambda p: p["code"])


# 회사 하나 = 함수 하나. 늘릴 때 여기에 더한다.
# 남은 4사 — SPA 2(메리츠·카카오페이)는 헤드리스 브라우저 결정이, 현대해상은 robots 문의가,
# DB손보는 robots 의 의도(User-agent 없는 allow-list)를 존중해 뺀 결정(RAG-048)이 먼저다
_ADAPTERS = {"samsung": _samsung, "kb": _kb, "nh": _nh}


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
                f"판매중 약관이 {len(products)}건뿐이다 (실측 24건). "
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
