"""국가법령정보 공동활용 Open API (DRF) — 조문 단위 XML.

  목록: lawSearch.do?OC={OC}&target=law&type=XML&query={법령명}   → 법령ID 찾기
  본문: lawService.do?OC={OC}&target=law&type=XML&ID={법령ID}     → 조문 단위 XML

RAG-011 에서 웹 원문(`law-animal-protection` 등)을 정식 경로로 확정했고 이 소스는 그것을 대체하지 않는다.
역할이 다르다 — 웹은 **사람이 열 수 있는 출처 링크**(답변에 그대로 싣는다, KPI), API 는 **조문 경계가
태그로 확정된 구조화 데이터**(청킹·`section` 채우기, RAG-004). 둘 다 두고 청킹 단계에서 API 쪽을 쓴다.

규격 검증 (2026-08-20) — 법제처 공식 매뉴얼이 공개한 샘플 키 `OC=test` 로 실제 응답을 받아 확인했다.
  검색 `lawSearch.do` : <LawSearch><resultCode>00</resultCode><law><법령명한글>(CDATA)</법령명한글>
                        <법령ID>000412</법령ID><법령일련번호>287795</법령일련번호>…
  본문 `lawService.do` : <법령><기본정보>(법령명_한글·시행일자·공포일자·공포번호·제개정구분·소관부처)
                        <조문단위 조문키="0015001"><조문번호>15</조문번호><조문여부>조문|전문</조문여부>
                          <조문제목>…<조문내용>제15조(…)  <항><항번호>①</항번호><항내용>…
                            <호><호내용>1. …</호내용></호>  <목><목내용>가. …</목내용></목>
  - 검색은 `법령명한글`(밑줄 없음), 본문은 `법령명_한글`(밑줄 있음)로 **이름이 다르다**
  - `조문여부`가 `전문`인 단위는 장·절 제목("제1장 총칙")이다. 조 수는 `조문`만 센다
  - 항·호·목의 내용에는 번호가 이미 붙어 있다(`① …`, `1. …`) — 따로 조립할 필요 없다
  - 검색의 `법령ID`(000412)로 본문을 부르면 현행 시행본이 온다.
    `법령일련번호`(287795)는 RAG-011 에서 웹 껍데기로 얻은 `lsiSeq` 와 같은 값이다
  - 웹 원문에 없던 **별표·서식이 `<별표단위>` 로 오고 HWP/PDF 파일 링크가 들어 있다**
    (`<별표서식파일링크>` 를 https://www.law.go.kr 뒤에 붙이면 내려받아진다).
    RAG-011 에서 미해결로 남긴 "과태료 부과기준 별표" 문제의 답이 여기 있다

수집 확인 완료 (2026-08-27 재수집, RAG-030) — 위의 규격 검증은 공용 샘플 키 `OC=test` 로 했지만
   실제 수집은 본인 OC 로 8건 전부 성공했다. 그때 확인한 두 가지를 기록해 둔다:
     1. `.meta.json` 의 source_url 에 OC 가 `***` 로 가려진다 (`config.redact()`)
     2. 응답 바이트가 호출마다 같다 — 타임스탬프 같은 것이 안 섞여 있어 지문(RAG-009)을
        원본 바이트 해시로 두어도 매번 CHANGED 가 뜨지 않는다

OC 발급 — open.law.go.kr 에서 신청하면 즉시 나온다. IP/도메인 등록은 **필수가 아니다**
(`OC=test` 가 등록 없이 이 PC 에서 동작하는 것으로 확인). 인증 실패 시 나오는 "IP주소 및 도메인주소를
등록해 주세요" 는 원인을 특정하지 않는 공통 안내문이라, 잘못된 OC 를 써도 똑같이 나온다.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from ...core import config, textutil
from ...core.fetch import FetchResult, Fetcher
from .. import _drf
from ..base import Extracted, Source, Target

# DRF 호출 규약(엔드포인트·인증 실패 판정·날짜 정규화)은 `sources/_drf.py` 에 있다.
# `ordinance-search`(target=ordin)가 같은 문을 쓰기 시작하면서 올렸다 — 본문 규격은
# 둘이 완전히 다르므로 공유하지 않는다. 이유는 _drf.py 의 모듈 도크스트링에 있다.
SEARCH = _drf.SEARCH
SERVICE = _drf.SERVICE

_RE_SPACE = re.compile(r"\s+")


def _norm(name: str) -> str:
    """법령명 비교용. '가축전염병예방법' 과 '가축전염병 예방법' 은 같은 법이다."""
    return _RE_SPACE.sub("", name)


class LawDrfApi(Source):
    id = "law-drf-api"
    domain = "law"
    category = "policy"
    subcategory = "law-articles"              # 법령별 값은 Target.meta 로 덮어쓴다
    source_type = "api"
    format = "xml"
    trust_level = "law"
    license = "공공누리 제1유형"

    # (법령명, slug 접미사, subcategory)
    LAWS = [
        ("동물보호법",            "animal-protection-act",    "animal-protection-act"),
        ("동물보호법 시행령",      "animal-protection-decree", "animal-protection-act"),
        ("동물보호법 시행규칙",    "animal-protection-rule",   "animal-protection-act"),
        ("가축전염병예방법",       "livestock-epidemic-act",    "livestock-epidemic-act"),
        ("가축전염병예방법 시행령", "livestock-epidemic-decree", "livestock-epidemic-act"),
        ("가축전염병예방법 시행규칙", "livestock-epidemic-rule", "livestock-epidemic-act"),
        ("수의사법",              "veterinarian-act",          "veterinarian-act"),
        ("자연공원법",            "natural-park-act",          "natural-park-act"),
    ]

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        if not config.LAW_OC:
            raise RuntimeError(_drf.OC_MISSING)

        targets: list[Target] = []
        for name, suffix, subcategory in self.LAWS:
            found = self._find_law(fetcher, name)
            targets.append(Target(
                url=f"{SERVICE}?OC={config.LAW_OC}&target=law&type=XML&ID={found['law_id']}",
                slug=f"{self.id}-{suffix}",
                ext="xml",
                meta={"title": name, "subcategory": subcategory, **found},
            ))
        return targets

    def _find_law(self, fetcher: Fetcher, name: str) -> dict[str, str | None]:
        """법령명으로 검색해 법령ID·법령일련번호·**현행 시행일자**를 얻는다.

        검색은 부분 일치라 '동물보호법' 하나로 시행령·시행규칙·무관한 법이 함께 나온다.
        공백을 지운 이름이 정확히 같은 것만 고른다 — 정식 명칭의 띄어쓰기가 흔들리기 때문
        ('가축전염병 예방법').

        시행일자를 여기서 가져오는 이유 (2026-08-20 실측) — 본문조회 `기본정보/시행일자` 는
        **현행 시행일이 아니다.** 동물보호법 시행령은 한 번 공포(2025-06-02)에 단계별 시행일이
        걸려 있어 `target=eflaw` 이력에 같은 일련번호가 20250602 / 20251203 / 20260603 세 번
        나온다. 본문조회는 그중 기준 레코드 날짜(20250602)를 주는데, 오늘 시행 중인 것은
        20260603 이고 그 값은 목록조회의 `현행` 레코드와 웹 원문 화면에만 있다.
        본문 자체는 같은 버전이다 — 조문 집합이 웹 원문과 정확히 일치하는 것으로 확인했다.
        """
        url = f"{SEARCH}?OC={config.LAW_OC}&target=law&type=XML&display=100&query={quote(name)}"
        res = fetcher.get(url)
        if not res.ok:
            raise RuntimeError(f"lawSearch HTTP {res.status}: {config.redact(url)}")

        _drf.check_auth_error(res.content)
        soup = BeautifulSoup(res.content, "xml")

        # 성공 응답은 <resultCode>00</resultCode><resultMsg>success</resultMsg>
        _drf.check_result_code(soup, "lawSearch")

        wanted = _norm(name)
        for law in soup.find_all("law"):
            got = _drf.text(law, "법령명한글", "법령명_한글", "법령명")
            if got and _norm(got) == wanted:
                law_id = _drf.text(law, "법령ID", "법령일련번호")
                if law_id:
                    return {
                        "law_id": law_id,
                        # 웹 원문(RAG-011)의 lsiSeq 와 같은 값이라 두 소스를 맞춰 볼 수 있다
                        "law_serial": _drf.text(law, "법령일련번호"),
                        "published_at": _drf.ymd(_drf.text(law, "시행일자")),
                    }
        raise RuntimeError(
            f"'{name}' 을 검색 결과에서 찾지 못함. 정식 명칭이 바뀌었거나 응답 규격이 다를 수 있다.\n"
            f"  응답 앞부분: {_drf.preview(res.content)}"
        )

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "xml")

        info = soup.find("기본정보")
        if info is None:
            raise RuntimeError(
                f"기본정보 태그 없음 — 규격이 다르다: {_drf.preview(res.content)}")

        title = _drf.text(info, "법령명_한글", "법령명한글", "법령명") or target.meta.get("title", "")

        # 시행일자는 목록조회에서 받아 온 값을 쓴다 — 본문조회의 것은 현행 시행일이 아니다
        # (_find_law 의 주석 참고). 목록에서 못 얻었을 때만 본문 값으로 떨어진다.
        record_date = _drf.ymd(_drf.text(info, "시행일자"))
        published = target.meta.get("published_at") or record_date

        extra: dict[str, object] = {
            "law_id": target.meta.get("law_id"),
            "law_serial": target.meta.get("law_serial"),
            "promulgated_at": _drf.ymd(_drf.text(info, "공포일자")),
            "promulgation_no": _drf.text(info, "공포번호"),
            "revision_kind": _drf.text(info, "제개정구분"),
            "ministry": _drf.text(info, "소관부처명"),
        }
        if record_date and record_date != published:
            # 단계별 시행일이 걸린 법령. 어느 쪽을 썼는지 남겨 두면 나중에 헷갈리지 않는다
            extra["record_date"] = record_date

        units = soup.find_all("조문단위")
        if not units:
            raise RuntimeError(f"조문단위 태그가 없음 — 규격 확인 필요: {_drf.preview(res.content)}")

        # 태그 목록을 한 번에 넘겨 **문서 순서대로** 받는다.
        # 태그별로 따로 돌면 조 안에서 항→호→목 이 각각 뭉쳐 나와 읽는 순서가 깨진다
        # (제2조의 '가. 포유류 / 나. 조류' 가 호 나열 뒤로 밀렸다).
        lines = [t for el in soup.find_all(["조문내용", "항내용", "호내용", "목내용"])
                 if (t := el.get_drf.text(strip=True))]

        # 조문여부: '조문' = 실제 조, '전문' = 장·절 제목("제1장 총칙"). 조 수는 전자만 센다
        # (동물보호법 = 조 103 + 장절 12 = 단위 115. 웹 원문의 div.lawcon 103개와 일치).
        extra["articles"] = sum(1 for u in units
                                if (f := u.find("조문여부")) is not None and f.get_drf.text(strip=True) == "조문")
        extra["units"] = len(units)

        # 부칙 — 시행일과 경과규정이 들어 있다. 웹 원문(RAG-011)도 본문에 포함하므로 맞춘다.
        addenda = soup.find_all("부칙단위")
        extra["addenda"] = len(addenda)
        lines += [t for el in addenda if (t := el.get_drf.text("\n", strip=True))]

        # 별표·서식 — **내용이 통째로 들어 있다** (`별표내용`). RAG-011 에서 웹 원문의 미해결로
        # 남겨 둔 "과태료 부과기준 별표" 문제의 답이 여기다. 웹 HTML 에는 제목과 파일 링크뿐이었다.
        # 시행규칙은 별표가 80개고 서식 양식이 대부분이라 본문 대비 비중이 크다.
        tables = soup.find_all("별표단위")
        extra["attachments"] = len(tables)
        lines += [t for el in tables if (t := el.get_drf.text("\n", strip=True))]

        # 조문키/조문번호는 RAG-004 의 section 후보다. 원본 XML 을 그대로 저장하므로
        # 여기서는 개수만 남기고 실제 section 부여는 파싱 단계에서 한다.
        text = textutil.squeeze("\n".join(lines))
        return Extracted(title=title, text=text, published_at=published,
                         cites=textutil.cites(text), extra=extra)
