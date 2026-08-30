"""보조금24 — 대한민국 공공서비스(혜택) 정보 Open API (data.go.kr 15113968).

  목록: api.odcloud.kr/api/gov24/v3/serviceList?cond[{필드}::LIKE]={키워드}
  상세: api.odcloud.kr/api/gov24/v3/serviceDetail?cond[서비스ID::EQ]={서비스ID}

`ordinance-search`(조례)가 지자체 지원의 **법적 근거**라면 이쪽은 **올해의 집행**이다 —
얼마를, 누구에게, 어디에 신청해서 받는지가 여기 있다. 그래서 `trust_level` 이 `official` 이고
조례(`law`)와 다르다. 둘을 한 값으로 묶지 않는 이유는 `data/README.md` 값 사전에 적어 뒀다.

────────────────────────────────────────────────────────────────────────────
정찰 (2026-08-28) — 활용신청 직후 실측
────────────────────────────────────────────────────────────────────────────
**전체 10,958건.** 카드가 "7,500여 개" 라고 적어 둔 것보다 늘었다. 그중 우리가 쓰는 것은
키워드로 걸러낸 **37건**뿐이라 규모는 문제가 안 된다.

`cond[필드::LIKE]=값` 으로 **서버가 걸러 준다.** 이게 이 소스의 전부다 — 안 되면 10,958건을
전부 받아 와야 하고 그건 일일 호출 한도(1,000회)를 넘는다. 지금 설계는
**목록 16회 + 상세 37회 = 53회**로 끝난다.

  ⚠️ **`totalCount` 는 필터를 무시하고 항상 10,958 을 준다.** 거른 결과의 개수가 아니다.
     이 값을 페이징 종료 조건으로 쓰면 110페이지를 돌게 된다 — `data` 가 `perPage` 보다
     적게 오는 것으로 끝을 판단한다.

**키워드에 `반려` 를 단독으로 쓰면 안 된다.** 행정 문서의 "반려"는 **返戾**(신청을 되돌려보냄)와
동음이의어다. 실측에서 `지원내용 LIKE 반려` 가 C형간염 확진검사비·산모신생아 건강관리·
ICT 돌봄서비스·군민안전보험을 끌고 왔다 — 전부 "신청이 반려된 경우" 문장이다. 그래서 아래
`KEYWORDS` 는 전부 복합어다. `반려식물 보급`(서울 강동구)도 같은 이유로 빠진다.

거른 결과 37건의 소관기관은 **시군구 28 · 광역시도 8 · 중앙 1** 이다. 조례(`ordinance-search`)가
기초지자체 중심이었던 것과 같은 분포이고, 두 소스가 같은 지자체를 근거·집행 양쪽에서 덮는다.

응답 규격
  목록 `serviceList` : 서비스ID · 서비스명 · 소관기관명/유형/코드 · 서비스분야 · 지원유형 ·
                       지원내용 · 지원대상 · 선정기준 · 신청방법 · 신청기한 · 접수기관 ·
                       전화문의 · 부서명 · 조회수 · 등록일시 · 수정일시 · **상세조회URL**
  상세 `serviceDetail`: 위 대부분 + 서비스목적 · 구비서류 · 공무원확인구비서류 ·
                        본인확인필요구비서류 · 문의처 · 온라인신청사이트URL ·
                        **법령** · **자치법규**

  - **`법령`·`자치법규` 가 이 소스를 코퍼스에 붙이는 고리다.** `유아학비` 예시가
    `유아교육법(제24조)||영유아보육법(제34조)` 처럼 오고, 지자체 것은
    `수원시 사회적 약자 반려동물 진료비등 지원 조례` 처럼 온다 — **우리가 이미 받아 둔
    조례 이름과 같은 문자열**이다. 지금은 텍스트로만 싣지만 나중에 문서 간 링크를 걸 자리다
  - 여러 값은 `||` 로 이어 온다 (`전화문의`, `법령`, `신청방법` …). 파서가 줄로 편다
  - `상세조회URL` 은 `https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{서비스ID}` 로 **유도된다**
    (표본 6/6 일치). 그래서 목록 행을 raw 에 안 남겨도 파서가 인용 링크를 만들 수 있다
  - `자치법규`·`법령` 이 없는 서비스는 `None` 이다 (빈 문자열이 아니다)

**Target 하나 = 상세조회 1회**로 잡았다. 목록 행은 `Target.meta` 로만 들고 간다 —
`store.save()` 가 `.meta.json` 에 고정 스키마만 쓰므로 파서까지 가지 않지만, 파서에 필요한
것(인용 링크)이 서비스ID 에서 유도되므로 잃는 것이 없다. 목록과 상세를 한 파일에 합쳐 저장하는
길도 있었지만 그러면 **원본이 우리가 만든 합성물**이 되어 RAG-008 의 "원본 불변"이 흐려진다.

일일 한도 — data.go.kr 개발계정은 **1,000회/일**이다. 이 소스는 53회를 쓴다. 실시간(파트②)이
같은 키로 같은 한도를 나눠 쓰므로(D-019 의 Redis 예산 카운터) 여유를 크게 두는 편이 맞다.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from ...core import config
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target
from ._pet_keywords import KEYWORDS

BASE = "https://api.odcloud.kr/api/gov24/v3"
LIST = f"{BASE}/serviceList"
DETAIL = f"{BASE}/serviceDetail"
GOV24 = "https://www.gov.kr/portal/rcvfvrSvc/dtlEx"

PAGE_SIZE = 100
MAX_PAGES = 20                       # 폭주 방지. 한 키워드가 100건을 넘는 일은 없었다

# 어느 필드를 훑을지. 제목만 보면 `동물등록`·`중성화` 가 각각 3건·4건뿐인데
# 지원내용까지 보면 17건·16건이 된다 — 사업명은 "취약계층 의료비 지원" 처럼 포괄적이고
# 무엇을 지원하는지는 본문에 있기 때문이다.
FIELDS = ("서비스명", "지원내용")

# **전부 복합어다.** 단독 `반려` 는 返戾(신청 반려)와 겹쳐 무관한 사업을 끌고 온다 (위 정찰).
# 목록은 `_pet_keywords` 에 있다 — `seoul-notice-api` 와 같은 것을 쓴다 (RAG-053 ④).

KEY_MISSING = (
    "DATA_GO_KR_KEY 미설정. data.go.kr 회원가입 후 이 API 의 '활용신청' 을 누르면\n"
    "  자동승인으로 즉시 열린다: https://www.data.go.kr/data/15113968/openapi.do\n"
    "  발급 후 .env 에 `DATA_GO_KR_KEY=발급받은키` 한 줄. (docs/data-sources.md §9)"
)


def _unauthorized(res: FetchResult) -> str | None:
    """odcloud 의 실패 코드를 사람 말로. `-3` 과 `-4` 를 구분하는 것이 요점이다.

    실측 (2026-08-27, 활용신청 전):
      없는 경로  → HTTP 404 {"code":-3,"msg":"등록되지 않은 서비스 입니다."}
      권한 없음  → HTTP 401 {"code":-4,"msg":"등록되지 않은 인증키 입니다."}

    `-4` 의 문구가 "인증키" 라 **키가 죽은 것처럼 읽히지만 실제로는 이 데이터셋에 활용신청이
    안 된 것**이다. 같은 키로 다른 API 는 200 을 준다. 이 구분을 안 해 주면 멀쩡한 키를
    의심하며 시간을 버린다.
    """
    try:
        body = json.loads(res.content)
    except (ValueError, TypeError):
        return f"HTTP {res.status} — 응답이 JSON 이 아니다: {res.content[:200]!r}"
    code, msg = body.get("code"), body.get("msg", "")
    if code == -4:
        return (f"활용신청이 안 됐다 (code -4: {msg})\n"
                "  **키가 죽은 것이 아니다** — 같은 키로 다른 data.go.kr API 는 동작한다.\n"
                "  data.go.kr 은 API 마다 따로 신청을 받는다. 자동승인이라 누르면 즉시 열린다:\n"
                "  https://www.data.go.kr/data/15113968/openapi.do")
    if code == -3:
        return (f"엔드포인트 경로가 틀렸다 (code -3: {msg})\n"
                "  권한 문제가 아니다 — 이 코드는 경로가 없을 때만 나온다.")
    return f"HTTP {res.status} code={code} msg={msg}"


class Benefit24Services(Source):
    id = "benefit24-services"
    domain = "subsidy"
    category = "policy"
    subcategory = "subsidy"
    source_type = "api"
    format = "json"
    trust_level = "official"                  # 조례(law)와 다르다 — 근거가 아니라 집행이다
    license = "공공누리 제1유형"

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        if not config.DATA_GO_KR_KEY:
            raise RuntimeError(KEY_MISSING)

        found: dict[str, dict] = {}           # 서비스ID → 목록 행
        matched: dict[str, list[str]] = {}    # 서비스ID → 걸린 (필드,키워드) 목록

        for field in FIELDS:
            for keyword in KEYWORDS:
                for row in self._search(fetcher, field, keyword):
                    sid = row.get("서비스ID")
                    if not sid:
                        raise RuntimeError(f"목록 행에 서비스ID 가 없다 — 규격 확인 필요: {row}")
                    found.setdefault(sid, row)
                    # 왜 걸렸는지를 남긴다. 키워드를 손볼 때 무엇이 사라지는지 보인다
                    matched.setdefault(sid, []).append(f"{field}:{keyword}")

        if not found:
            raise RuntimeError(
                "키워드 " + " ".join(KEYWORDS) + " 로 한 건도 못 찾았다.\n"
                "  전체 10,958건 중 37건이 걸리던 조건이라, 0건이면 필터 규격이 바뀐 것이다.")

        targets: list[Target] = []
        for sid, row in sorted(found.items()):
            targets.append(Target(
                url=(f"{DETAIL}?cond%5B{_q('서비스ID')}%3A%3AEQ%5D={sid}"
                     f"&page=1&perPage=1&serviceKey={_q(config.DATA_GO_KR_KEY)}"),
                slug=f"{self.id}-{sid}",
                ext="json",
                meta={
                    "title": row.get("서비스명"),
                    "service_id": sid,
                    "org": row.get("소관기관명"),
                    "org_type": row.get("소관기관유형"),
                    "field": row.get("서비스분야"),
                    "support_type": row.get("지원유형"),
                    "deadline": row.get("신청기한"),
                    # 목록에도 있지만 서비스ID 에서 유도된다 — 파서는 유도해서 쓴다
                    "citation_url": f"{GOV24}/{sid}",
                    "matched_by": matched[sid],
                },
            ))
        return targets

    def _search(self, fetcher: Fetcher, field: str, keyword: str) -> list[dict]:
        """한 (필드, 키워드) 조합을 끝까지 페이징."""
        rows: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            # `cond[…]` 의 대괄호와 한글 필드명은 fetcher 가 그대로 보내도 되도록 미리 인코딩한다
            url = (f"{LIST}?cond%5B{_q(field)}%3A%3ALIKE%5D={_q(keyword)}"
                   f"&page={page}&perPage={PAGE_SIZE}&serviceKey={_q(config.DATA_GO_KR_KEY)}")
            res = fetcher.get(url)
            if not res.ok:
                raise RuntimeError(
                    f"serviceList {field}~{keyword}: {_unauthorized(res)}\n"
                    f"  {config.redact(url)}")

            got = (json.loads(res.content).get("data")) or []
            rows += got
            # **`totalCount` 를 쓰지 않는다** — 필터를 무시하고 늘 전체(10,958)를 준다.
            # 받은 개수가 perPage 보다 적으면 그게 마지막 장이다
            if len(got) < PAGE_SIZE:
                break
        else:
            raise RuntimeError(
                f"{field}~{keyword} 가 {MAX_PAGES} 페이지를 넘었다. 필터가 안 먹는지 확인할 것.")
        return rows

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        body = json.loads(res.content)
        rows = body.get("data") or []
        if not rows:
            raise RuntimeError(
                f"서비스ID {target.meta.get('service_id')} 의 상세가 비었다. "
                "목록에는 있는데 상세가 없으면 규격이나 데이터가 바뀐 것이다.")
        row = rows[0]

        got_id = row.get("서비스ID")
        if got_id != target.meta.get("service_id"):
            # `cond[서비스ID::EQ]` 가 안 먹으면 **전체 목록의 첫 행**이 온다. 그러면 37개 문서가
            # 전부 같은 내용이 되는데, 각자 다른 slug 로 저장돼 눈으로는 안 보인다
            raise RuntimeError(
                f"상세의 서비스ID 가 다르다: 요청 {target.meta.get('service_id')} → 응답 {got_id}. "
                "cond[서비스ID::EQ] 필터가 안 먹었을 수 있다.")

        title = row.get("서비스명") or target.meta.get("title") or ""
        # `수정일시` 는 상세가 `2026-01-29`, 목록이 `20260129201825` 로 형식이 다르다.
        # 답변에 "언제 기준 정보인지" 를 실으려면 이 값이 필요하다 (카드 메모 ⑤).
        published = _ymd(row.get("수정일시"))

        # 검색·지문에 쓸 본문. **필드명을 붙여 넘긴다** — 조문 번호가 없는 소스라
        # 청킹 단계에서 필드명이 곧 경계가 된다 (파서가 같은 순서를 쓴다).
        text = "\n".join(f"{k}: {_flat(row[k])}" for k in _TEXT_FIELDS if row.get(k))

        return Extracted(
            title=title, text=text, published_at=published,
            extra={
                "service_id": got_id,
                "org": row.get("소관기관명") or target.meta.get("org"),
                "org_type": target.meta.get("org_type"),
                "field": target.meta.get("field"),
                "support_type": row.get("지원유형"),
                "deadline": row.get("신청기한"),
                "citation_url": target.meta.get("citation_url"),
                # 코퍼스 안의 조례·법령과 이어지는 고리 (위 정찰)
                "ordinance": row.get("자치법규"),
                "law": row.get("법령"),
                "matched_by": target.meta.get("matched_by"),
            },
        )


# 본문으로 삼을 필드와 그 순서. 사람이 묻는 순서에 가깝게 둔다 —
# 무엇을(지원내용) · 누가(지원대상·선정기준) · 어떻게(신청방법·기한·구비서류) · 어디에(접수·문의).
_TEXT_FIELDS = (
    "서비스명", "서비스목적", "지원내용", "지원대상", "선정기준", "지원유형",
    "신청방법", "신청기한", "구비서류", "접수기관명", "문의처",
    "온라인신청사이트URL", "법령", "자치법규",
)


def _q(s: str) -> str:
    """URL 에 그대로 박아 넣기 위한 인코딩.

    **키에도 반드시 씌운다.** `config.normalize_key` 를 지난 키는 Decoding 형태(base64)라
    `+` `/` `=` 를 품고 있고, 그대로 쿼리스트링에 넣으면 `+` 가 공백으로 해석돼 키가 깨진다.
    증상은 `401 -4 등록되지 않은 인증키` 라 **활용신청을 안 한 것과 똑같이 보인다**(실측).
    httpx 의 `params=` 를 쓰면 자동으로 되는 일이지만, 여기서는 `Fetcher` 가 URL 하나만
    받으므로(robots 판정·간격 조절이 URL 기준이다) 손으로 씌운다.
    """
    return quote(s, safe="")


def _flat(v: object) -> str:
    """`||` 로 이어 온 여러 값을 ` / ` 로 편다. 줄바꿈·연속 공백도 한 칸으로."""
    s = str(v).replace("||", " / ")
    return " ".join(s.split())


def _ymd(raw: object) -> str | None:
    """`2026-01-29` 또는 `20260129201825` → `2026-01-29`."""
    d = re.sub(r"\D", "", str(raw or ""))
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else None
