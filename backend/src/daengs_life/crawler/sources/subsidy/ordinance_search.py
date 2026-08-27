"""자치법규(조례) DRF Open API — 반려동물 조례 전수.

  목록: lawSearch.do?OC={OC}&target=ordin&type=XML&query=반려동물&display=100&page=N
  본문: lawService.do?OC={OC}&target=ordin&type=XML&MST={자치법규일련번호}

지자체 지원사업의 **법적 근거**를 담당한다. 공고문(`seoul-notice-api`)이 "올해 얼마를 준다"면
조례는 "왜 줄 수 있는가"이고, 후자는 연도가 바뀌어도 살아 있다. `trust_level` 이 `law` 인 이유다.

────────────────────────────────────────────────────────────────────────────
정찰 (2026-08-27) — 수집 범위를 정한 근거
────────────────────────────────────────────────────────────────────────────
`search` 파라미터가 검색 대상을 정한다. **기본값은 1(자치법규명)이고 2는 본문 전문검색이다.**
이 차이가 카드 크기를 통째로 바꾼다:

    query        search=1 (제목)   search=2 (본문)
    반려동물          208            1,077
    동물보호          467                -
    중성화              0              195
    동물등록            0            1,483

`중성화`·`동물등록` 이 제목에서 0인 것은 조례 제목이 "OO시 반려동물 보호 및 복지 조례" 처럼
포괄적으로 붙기 때문이다. 지원 내용은 그 안의 조에 들어 있다.

**`search=1` 의 `반려동물` 208건 전수를 1차 범위로 잡았다.** 근거 셋:

  ① 크기가 안 터진다. 20건 표본에서 조 평균 10.3개(최소 5, 최대 20) · 평균 2,750자였다.
     208건 ≈ **조문 2,150개 · 57만 자**. 지금 코퍼스가 청크 약 1,400 이므로 3배가 되지만
     자릿수는 그대로다 — 카드 메모 ②의 "HNSW 를 내렸다 다시 만든다"는 필요 없다(수천 행).
     `search=2` 로 넓히면 1,077건 ≈ 조문 11,000개가 되어 이 카드에서 안 끝난다.
  ② 광역으로 끊으면 커버리지가 무너진다. 208건의 지자체는 135곳인데 **광역 17개 시·도는
     34건(16%)뿐**이다. 지원사업 조례는 기초지자체가 만든다 — "우리 동네" 질문에 답하는 것이
     이 도메인의 목적이므로 기초를 빼면 소스의 의미가 없다. 전수가 208건이라 나눌 이유도 없다.
  ③ 제목에 `반려동물` 이 있는 조례가 지원사업 근거의 본류다. 본문검색으로 걸리는 나머지
     869건은 대부분 다른 주제 조례가 반려동물을 스치듯 언급한 것이다. 재현율을 더 올리는 것은
     `search=2` 를 별도 카드로 두고 판단한다.

응답 규격 — **법령(`target=law`)과 태그가 하나도 안 겹친다.** `law_drf_api.py` 를 복사해 오면
안 되고, 공유되는 것은 호출 규약뿐이라 그것만 `sources/_drf.py` 로 올렸다.

    목록 <OrdinSearch><totalCnt><law id="1">
           <자치법규일련번호>1834553   ← 본문조회 MST. 개정마다 새로 발급된다
           <자치법규ID>2232894        ← 개정을 건너 고정. slug 는 이것을 쓴다
           <자치법규명> <지자체기관명> <자치법규종류>조례</자치법규종류>
           <공포일자> <시행일자> <제개정구분명> <자치법규분야명>
           <자치법규상세링크>/DRF/lawService.do?OC={우리 OC}&…  ← **OC 가 평문으로 박혀 온다**
    본문 <LawService><자치법규기본정보>(자치법규종류가 여기서는 코드 'C0001' 이다)
           <조문><조 조문번호="000100"><조문번호><조문여부>Y</조문여부><조제목><조내용>
           <부칙><부칙공포일자><부칙공포번호><부칙내용>

  - **항·호·목 태그가 없다.** 법령 XML 은 `<항><호><목>` 으로 경계가 태그였지만, 조례는
    "1.…" "2.…" 가 `<조내용>` 안의 줄바꿈 텍스트다. **조가 유일한 구조 경계**이므로
    RAG-004 의 조문 단위 청킹은 그대로 성립하지만 항 단위로는 더 못 쪼갠다
  - `조문여부` 는 `Y`(조문) / `N`(장·절 제목)이다. 법령의 `조문`/`전문` 과 값만 다르고
    역할은 같다. 30개 문서 348개 조 표본에서 `N` 4건은 전부 "제1장 총칙" 류였고
    그때 `조문번호` 가 `000000` 이다
  - `조문번호` 는 **6자리 고정, 앞 4 = 조 번호 · 뒤 2 = 가지번호**다 (`000100` = 제1조).
    같은 표본 348건에서 조내용 앞머리("제N조의M")와 한 건도 어긋나지 않았다
  - `자치법규종류` 는 **목록에서 읽어야 한다.** 본문은 코드(`C0001`)로 주고 코드표가 없다
  - `담당부서명`·`전화번호` 는 표본 전부 빈 값이었다. 법령 웹 원문에서 연락처를 지웠던
    문제(RAG-011 의 `.cont_subtit`)가 여기서는 애초에 안 생긴다
  - 목록의 `자치법규상세링크` 에 OC 가 그대로 들어온다. **meta 로 넘기지 않는다** — 넘기면
    `redact()` 를 통과하더라도 굳이 커밋되는 파일에 키 자리를 하나 더 만드는 셈이다

인용 URL (카드 메모 ④) — `citation_url` 은 사람이 열 수 있어야 한다.
  `https://www.law.go.kr/자치법규/{공백제거 자치법규명}` 이 200 을 준다. 법령 쪽
  `www.law.go.kr/법령/{법령명}` 과 같은 패턴의 껍데기(1.3KB, 본문은 iframe)이고, 브라우저로
  열면 정상적으로 보인다. `LSW/ordinInfoR.do?ordinSeq={자치법규일련번호}` 도 200(22KB 서버렌더)
  이지만 **`ordinSeq` 는 개정마다 바뀌는 일련번호라 링크가 늙는다** — 법령에서 `lsiSeq` 를
  시드에 고정하지 않기로 한 것(RAG-011)과 같은 이유로 **이름 URL 을 쓴다.**
"""
from __future__ import annotations

from urllib.parse import quote

from bs4 import BeautifulSoup

from ...core import config, textutil
from ...core.fetch import FetchResult, Fetcher
from .. import _drf
from ..base import Extracted, Source, Target

QUERY = "반려동물"
PAGE_SIZE = 100                     # DRF 목록조회 display 상한
MAX_PAGES = 20                      # 폭주 방지. 208건이면 3페이지다
WEB = "https://www.law.go.kr/자치법규"


class OrdinanceSearch(Source):
    id = "ordinance-search"
    domain = "subsidy"
    category = "policy"
    subcategory = "ordinance"
    source_type = "api"
    format = "xml"
    trust_level = "law"
    license = "공공누리 제1유형"

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        if not config.LAW_OC:
            raise RuntimeError(_drf.OC_MISSING)

        targets: list[Target] = []
        seen: set[str] = set()
        total: int | None = None
        records = 0                     # 중복 제거 **전** 레코드 수. totalCnt 와 대조할 값이다

        for page in range(1, MAX_PAGES + 1):
            url = (f"{_drf.SEARCH}?OC={config.LAW_OC}&target=ordin&type=XML"
                   f"&display={PAGE_SIZE}&page={page}&query={quote(QUERY)}")
            res = fetcher.get(url)
            if not res.ok:
                raise RuntimeError(f"lawSearch HTTP {res.status}: {config.redact(url)}")

            _drf.check_auth_error(res.content)
            soup = BeautifulSoup(res.content, "xml")
            _drf.check_result_code(soup, "lawSearch(ordin)")

            if total is None:
                total = int(_drf.text(soup, "totalCnt") or 0)
                if not total:
                    raise RuntimeError(
                        f"'{QUERY}' 검색 결과 0건 — 규격이나 파라미터가 바뀌었을 수 있다.\n"
                        f"  응답 앞부분: {_drf.preview(res.content)}")

            laws = soup.find_all("law")
            if not laws:
                break

            records += len(laws)
            for law in laws:
                mst = _drf.text(law, "자치법규일련번호")
                ordin_id = _drf.text(law, "자치법규ID")
                name = _drf.text(law, "자치법규명")
                if not (mst and ordin_id and name):
                    raise RuntimeError(
                        f"목록 레코드에 일련번호/ID/이름이 없음 — 규격 확인 필요: {law}")
                if ordin_id in seen:          # 페이지 경계에서 같은 건이 다시 오는 경우 방어
                    continue
                seen.add(ordin_id)

                targets.append(Target(
                    # 개정을 건너 고정인 것은 자치법규ID 지만, 본문조회 키는 MST 다.
                    # 재수집 때 MST 가 바뀌면 같은 slug 에 새 날짜 파일이 생긴다 (data/README 규칙 1)
                    url=f"{_drf.SERVICE}?OC={config.LAW_OC}&target=ordin&type=XML&MST={mst}",
                    slug=f"{self.id}-{ordin_id}",
                    ext="xml",
                    meta={
                        "title": name,
                        "ordin_id": ordin_id,
                        "ordin_serial": mst,
                        "org": _drf.text(law, "지자체기관명"),
                        # 본문은 코드(C0001)로 주므로 사람이 읽는 값은 목록에서만 얻는다
                        "ordin_kind": _drf.text(law, "자치법규종류"),
                        "field": _drf.text(law, "자치법규분야명"),
                        "published_at": _drf.ymd(_drf.text(law, "시행일자")),
                        # 퍼센트 인코딩하지 않는다 — 이 값은 답변에 그대로 실리는 사람용
                        # 링크이고, `law_animal_protection` 의 `/법령/{법령명}` 도 한글 그대로다
                        "citation_url": f"{WEB}/{name.replace(' ', '')}",
                    },
                ))

            if len(laws) < PAGE_SIZE or records >= total:
                break
        else:
            raise RuntimeError(
                f"목록조회가 {MAX_PAGES} 페이지를 넘었다 (총 {total}건). 페이징이 끝나지 않는다.")

        # 조용히 덜 받는 것이 이 소스에서 제일 위험하다 — 답변의 커버리지가 줄어드는데
        # 로그에는 성공만 남는다. 그래서 세어서 다르면 멈춘다.
        #
        # 대조하는 것은 **중복 제거 전** 레코드 수다. 중복 제거까지 마친 수로 대조하면 방어
        # 장치가 스스로를 실패로 만든다 — API 가 같은 조례를 두 번 주는 날 수집이 통째로 막힌다.
        # 중복은 정상 범위로 보고 걸러 두기만 하고, "덜 받았는가"만 예외로 만든다.
        if total is not None and records != total:
            raise RuntimeError(
                f"목록 건수 불일치: totalCnt={total}, 받은 레코드={records}. "
                "페이징이 중간에 끊겼을 수 있다.")
        return targets

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "xml")

        info = soup.find("자치법규기본정보")
        if info is None:
            raise RuntimeError(
                f"자치법규기본정보 태그 없음 — 규격이 다르다: {_drf.preview(res.content)}")

        title = _drf.text(info, "자치법규명") or target.meta.get("title", "")
        # 시행일자는 목록·본문 둘 다 준다. 목록 값을 우선한다 — 법령에서 본문의 시행일자가
        # 현행 시행일이 아니었던 전례(law_drf_api._find_law)가 있어 기준을 하나로 맞춘다.
        published = target.meta.get("published_at") or _drf.ymd(_drf.text(info, "시행일자"))

        articles = soup.find_all("조")
        if not articles:
            raise RuntimeError(f"<조> 태그가 없음 — 규격 확인 필요: {_drf.preview(res.content)}")

        extra: dict[str, object] = {
            "ordin_id": target.meta.get("ordin_id"),
            "ordin_serial": target.meta.get("ordin_serial"),
            "ordin_kind": target.meta.get("ordin_kind"),
            "org": target.meta.get("org") or _drf.text(info, "지자체기관명"),
            "field": target.meta.get("field"),
            "citation_url": target.meta.get("citation_url"),
            "promulgated_at": _drf.ymd(_drf.text(info, "공포일자")),
            "promulgation_no": _drf.text(info, "공포번호"),
            "revision_kind": _drf.text(info, "제개정정보"),
            "articles": len(articles),
        }

        # 조내용에는 조 번호와 제목이 이미 들어 있다("제1조(목적) 이 조례는…"). 조제목을 따로
        # 앞에 붙이면 제목이 두 번 나온다. 항·호는 조내용 안의 줄바꿈이라 그대로 살린다.
        lines = [t for el in soup.find_all("조내용") if (t := el.get_text("\n", strip=True))]
        extra["first_article"] = lines[0][:60] if lines else None

        # 부칙 — 시행일과 경과규정. 법령 쪽(law_drf_api)과 맞춘다.
        addenda = soup.find_all("부칙내용")
        extra["addenda"] = len(addenda)
        lines += [t for el in addenda if (t := el.get_text("\n", strip=True))]

        text = textutil.squeeze("\n".join(lines))
        return Extracted(title=title, text=text, published_at=published,
                         cites=textutil.cites(text), extra=extra)
