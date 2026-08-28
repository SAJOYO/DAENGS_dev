"""손해보험협회 상품비교공시 — 반려동물보험(`tptyCode=PB27`).

  목록(화면): kpub.knia.or.kr/popup/disclosurePopup.do?tabType=1&tptyCode=PB27
  데이터    : kpub.knia.or.kr/popup/disclosureList.do?…&pCode={회사코드}

`insurer-terms-pdfs`(약관 원문)와 **다른 층**이다. 약관이 "무엇이 보장되는가"의 원본이라면
여기는 **판매중 상품 전체를 한 자리에서 비교한 수치**다 — 담보명·지급사유·지급액·보험료·
자기부담금이 회사를 가로질러 같은 모양으로 온다. 약관 PDF 없이도 답할 수 있는 질문이 많고,
그것이 이 소스를 먼저 붙이는 이유다 (#50).

────────────────────────────────────────────────────────────────────────────
정찰 (2026-08-28) — 실측
────────────────────────────────────────────────────────────────────────────
**화면의 표는 비어 있다.** `disclosurePopup.do` 를 받아 봐야 `<thead>` 만 있고 행이 없다.
데이터는 같은 쿼리를 `disclosureList.do` 로 보내면 온다 (225KB). 이걸 모르면 "공시가 없다" 는
결론으로 샌다.

⚠️ **응답이 JSON 이 아니다.** `Content-Type: text/plain` 인데 내용은 **자바스크립트 객체
리터럴**이라 `json.loads` 가 죽는다. `core.jsobject.loads` 를 쓴다 — 그 모듈에 이유가 있다.

**216행 = 상품 48 × 담보.** 상품 수준 값(회사·상품명·보험료·특이사항)은 그 상품의 모든 행에
같은 값으로 반복되고, 행마다 다른 것은 `TP_PAY_NAME`·`TP_PAY_REASON`·`TP_PAY`(담보명·지급사유·
지급액)와 순번뿐이다. 파서가 이 축으로 되접는다.

**판매사는 7사다 — 9사가 아니다.**

  N01 메리츠화재 · N08 삼성화재 · N09 현대해상 · N10 KB손보 · N11 DB손보 ·
  N70 카카오페이손해보험 · N71 농협손보

시드에 적혀 있던 9사(한화·롯데·캐롯 포함)는 **근거가 없었다.** 그 셋은 이 공시에 없고
대신 카카오페이손보가 있다. 그래서 **회사 목록을 상수로 박지 않고 응답에서 읽는다** —
다음에 판매사가 바뀌어도 코드를 고칠 일이 없고, 바뀐 사실이 로그에 드러난다.

`Target` 하나 = **회사 하나**다. `pCode` 하나만 넘기면 그 회사 것만 온다(실측: N01→14행/4상품,
N71→44행/8상품). 상품 단위로 쪼개지 않는 이유는 그러면 같은 URL 을 48번 받아야 하기 때문이고,
회사보다 크게 묶지 않는 이유는 `citation_url`(그 회사 공시실)이 회사 단위이기 때문이다.
발견 1회 + 회사 7회 = **8요청**으로 끝난다.

**`published_at` 이 없다.** 페이지 어디에도 공시 기준일자가 없다(날짜 문자열 0건). 상품명 끝의
`2604`·`(2605.1)` 은 그 상품의 개정 연월이지 공시 시점이 아니라 쓰지 않는다. 언제 기준인지는
`.meta.json` 의 `fetched_at` 만 말해 준다 — 갱신 주기가 분기 1회 수준이라(§7) 큰 문제는 아니지만,
답변에 기준일을 실을 수 없다는 뜻이라 여기 적어 둔다.

**`format` 은 `json` 이다.** 엄밀히는 JS 리터럴이지만 값 사전이 닫힌 목록이고(`data/README.md`),
새 값을 만들면 그것을 아는 곳이 여기뿐이라 손해가 더 크다. 대신 지문(sha256)이 원본 바이트
기준이 되는 분기(`store.save`)는 이 값으로 정확히 맞는다.
"""
from __future__ import annotations

from ...core import jsobject
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

LIST = "https://kpub.knia.or.kr/popup/disclosureList.do"
POPUP = "https://kpub.knia.or.kr/popup/disclosurePopup.do"
TPTY = "PB27"                                # 반려동물보험 탭

# 이 개수를 벗어나면 규격이나 시장이 바뀐 것이다. 조용히 지나가지 않게 폭을 좁게 잡는다
MIN_FIRMS = 3
MIN_ROWS = 20


def _query(p_code: str = "") -> str:
    """`goSearch` 가 만드는 GET 과 같은 모양. 채널은 비우면 전체다 (실측)."""
    return (f"?tabType=1&tptyCode={TPTY}&detailYn=Y"
            f"&pCode={p_code}&prdNm=&payNm=&payReason=")


def read_rows(body: bytes) -> list[dict]:
    """응답 바이트 → 행 목록. 소스와 파서가 같은 함수를 쓴다."""
    data = jsobject.loads(body.decode("utf-8"))
    if not isinstance(data, dict) or "list" not in data:
        raise RuntimeError(f"응답에 list 가 없다 — 규격 확인 필요: {str(data)[:200]}")
    rows = data["list"]
    if not isinstance(rows, list):
        raise RuntimeError(f"list 가 배열이 아니다: {type(rows).__name__}")
    return rows


class KniaDisclosure(Source):
    id = "knia-disclosure"
    domain = "insurance"
    category = "policy"
    subcategory = "insurance"
    source_type = "api"
    format = "json"
    trust_level = "official"                 # 협회 공시는 기관 안내다. 약관 원문(law)이 아니다
    license = ""                             # 협회 공시 — 이용조건 별도 표기 없음. 인용 시 출처 표기

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        res = fetcher.get(LIST + _query())
        if not res.ok:
            raise RuntimeError(f"목록 조회 실패: HTTP {res.status} {LIST}")
        rows = read_rows(res.content)

        if len(rows) < MIN_ROWS:
            raise RuntimeError(
                f"전체 {len(rows)}행뿐이다 (실측 216행). 필터나 규격이 바뀐 것으로 본다.")

        # **회사 목록을 응답에서 읽는다.** 상수로 박으면 판매사가 바뀐 것을 못 알아챈다 —
        # 시드의 '9사' 가 정확히 그렇게 틀려 있었다 (위 정찰)
        firms: dict[str, str] = {}
        for row in rows:
            code, name = row.get("P_CODE"), row.get("P_CODE_NM")
            if not code or not name:
                raise RuntimeError(f"행에 회사 코드/이름이 없다 — 규격 확인 필요: {row}")
            firms[str(code)] = str(name)

        if len(firms) < MIN_FIRMS:
            raise RuntimeError(
                f"판매사가 {len(firms)}곳뿐이다 ({', '.join(firms.values())}). "
                "실측은 7곳이었다 — 필터가 안 먹었는지 확인할 것.")

        return [
            Target(
                url=LIST + _query(code),
                slug=f"{self.id}-{code}",
                ext="json",
                meta={
                    "title": f"{name} 반려동물보험 공시",
                    "p_code": code,
                    "p_code_nm": name,
                    "notes": f"손해보험협회 상품비교공시 {TPTY}(반려동물보험) — {name}",
                },
            )
            for code, name in sorted(firms.items())
        ]

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        rows = read_rows(res.content)
        want = target.meta.get("p_code")
        if not rows:
            raise RuntimeError(
                f"{target.meta.get('p_code_nm')}({want}) 의 행이 비었다. "
                "발견 때는 있었는데 사라졌다면 pCode 필터가 안 먹은 것이다.")

        # `pCode` 가 안 먹으면 **전체 216행**이 온다. 그러면 회사 7개 문서가 전부 같은 내용이
        # 되는데 각자 다른 slug 로 저장돼 눈으로는 안 보인다 (benefit24 의 `서비스ID` 검증과 같다)
        got = {str(r.get("P_CODE")) for r in rows}
        if got != {str(want)}:
            raise RuntimeError(
                f"pCode={want} 로 물었는데 응답에 {sorted(got)} 이 섞여 있다 — 필터가 안 먹었다.")

        products = {str(r.get("TP_CODE")) for r in rows}
        name = str(rows[0].get("P_CODE_NM") or target.meta.get("p_code_nm") or "")
        title = f"{name} 반려동물보험 공시"

        # dry-run 미리보기용. 지문(sha256)은 `format=json` 이라 원본 바이트로 잡히므로
        # 이 텍스트가 변경 감지에 쓰이지는 않는다 (store.save)
        lines = [f"{title} — 상품 {len(products)} · 담보 {len(rows)}"]
        for row in rows[:12]:
            lines.append(f"{row.get('TP_NAME')} | {row.get('TP_PAY_NAME')} | {row.get('TP_PAY')}")

        return Extracted(
            title=title,
            text="\n".join(lines),
            published_at=None,               # 공시 기준일자가 어디에도 없다 (위 정찰)
            extra={"p_code": want, "p_code_nm": name,
                   "products": len(products), "coverages": len(rows)},
        )
