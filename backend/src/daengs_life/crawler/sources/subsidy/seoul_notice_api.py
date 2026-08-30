"""서울시 고시공고 — 열린데이터광장 Open API (RAG-053).

  목록: openapi.seoul.go.kr:8088/{KEY}/json/tvvWcmBoardB0277New/{START}/{END}/
  원본: 같은 서비스의 **한 행짜리** 응답 ({i}/{i}/)

목적은 **신규 지원사업 탐지**다. 조례(`ordinance-search`)와 보조금24(`benefit24-services`)는
이미 있는 제도를 훑지만, 고시공고는 새로 생기는 것이 **먼저** 나타나는 자리다.

────────────────────────────────────────────────────────────────────────────
정찰 (2026-08-30) — 시드가 가리키던 데이터셋은 종료예정이었다
────────────────────────────────────────────────────────────────────────────
시드의 OA-2482(`ListNewsNotice`)는 카탈로그(`SearchOpenAPIService`)에 **"(종료예정)"** 으로
올라 있고 **최근 50건**만 준다. 후속은 OA-23050 `tvvWcmBoardB0277New` — 전체 아카이브
(12,133건), 본문이 HTML 이 아니라 **텍스트로 정리돼** 오고, `DEADLINE_DATE`(공고마감일)와
첨부 URL 5칸이 있다. 이쪽을 쓴다. 서비스명은 페이지에 없고(`svcNm = ''`) 카탈로그 API 로만
알 수 있어서, 시드 `notes` 에 적어 뒀다.

**필터 인자는 없다.** 옛 API 의 `BOARD_ID`·`TITLE`(선택)은 새 API 에 없고, 경로에 더 붙여도
무시된다(실측). 그래서 **최신 1,000건을 한 요청**으로 받아 여기서 거른다 — 하루 7.5건이라
1,000건이 약 6개월이고, 그 안에서 반려동물 키워드에 걸린 것은 **1건**이었다
(2026-04-02 항생제 내성균 모니터링 참여 모집). 수확이 적은 소스라는 뜻이고, 그래서 요청을
늘리지 않는다. 정렬은 `BOARD_ID` 내림차순(실측 1,000건 전부 단조).

**원본 1건 = 한 행짜리 응답.** 공고 하나를 따로 받는 URL 이 없다 — 시청 게시판은
`#view/{BOARD_ID}` 해시 라우팅(SPA)이라 본문이 HTML 에 없고, 첨부(seoulboard.seoul.go.kr)는
robots 가 `Disallow: /` 라 받지 않는다 (docs/data-sources.md §12). 남는 것이 목록의 `{i}/{i}/`
슬라이스다. 인덱스는 새 글이 올라오면 밀리므로 **`extract()` 가 `BOARD_ID` 를 대조해서 다르면
시끄럽게 실패한다** — 새벽 4시에 공고가 올라오는 일은 드물지만 조용히 엉뚱한 글을 저장하는
것보다 실패가 낫다. 여러 개가 걸려도 요청은 각각 1회다.

⚠ **지문은 본문 텍스트다** (`fingerprint = "text"`). 응답에 `list_total_count` 가 박혀 있어
바이트 해시면 **글이 하나 올라올 때마다 모든 공고가 `changed`** 가 된다 — 매일이다. 그러면
C3(#66)의 개정 알림이 이 소스에서 매일 울린다. `store.py` 가 이 속성을 보고 텍스트 지문을 쓴다.

인용 URL 은 `https://www.seoul.go.kr/news/news_notice.do#view/{BOARD_ID}` 다 (페이지 JS 가
`#view/1111 hash로 넘어올경우` 를 처리한다). API 주소를 인용에 쓰면 키가 든 URL 이 답변에 실린다.
"""
from __future__ import annotations

import json
import re

from ...core import config
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target
from ._pet_keywords import KEYWORDS, matches

SERVICE = "tvvWcmBoardB0277New"                 # OA-23050. 옛 OA-2482 `ListNewsNotice` 는 종료예정
BASE = "http://openapi.seoul.go.kr:8088"
NOTICE_PAGE = "https://www.seoul.go.kr/news/news_notice.do#view"

# 한 요청으로 볼 최신 글 수 = 소급 범위. 하루 7.5건이라 약 6개월. 늘리지 않는 이유는 위 정찰.
WINDOW = 1000

KEY_MISSING = (
    "SEOUL_OPEN_DATA_KEY 미설정. 서울 열린데이터광장(data.seoul.go.kr) 로그인 → 마이페이지 →\n"
    "  인증키 신청(무료, 즉시). 발급 후 .env 에 `SEOUL_OPEN_DATA_KEY=발급받은키` 한 줄.\n"
    "  (docs/data-sources.md §9)"
)


def _url(start: int, end: int) -> str:
    return f"{BASE}/{config.SEOUL_OPEN_DATA_KEY}/json/{SERVICE}/{start}/{end}/"


def _rows(res: FetchResult, *, what: str) -> list[dict]:
    """응답 → 행 목록. 실패 코드는 사람 말로 — `RESULT.CODE` 가 최상위에 오면 실패다.

    성공은 `{"tvvWcmBoardB0277New": {"list_total_count": N, "RESULT": {...}, "row": [...]}}`,
    실패는 `{"RESULT": {"CODE": "ERROR-310", ...}}` 처럼 **서비스명 키가 없다.**
    """
    try:
        body = json.loads(res.content)
    except (ValueError, TypeError):
        raise RuntimeError(f"{what}: 응답이 JSON 이 아니다: {res.content[:200]!r}") from None
    if SERVICE not in body:
        r = body.get("RESULT") or {}
        raise RuntimeError(
            f"{what}: {r.get('CODE')} {r.get('MESSAGE', '')}\n"
            "  INFO-100 이면 키, ERROR-310/500 이면 서비스명이 바뀐 것이다 — "
            "카탈로그(SearchOpenAPIService)에서 OA-23050 을 다시 찾을 것.")
    payload = body[SERVICE]
    code = (payload.get("RESULT") or {}).get("CODE")
    if code != "INFO-000":
        raise RuntimeError(f"{what}: {code} {(payload.get('RESULT') or {}).get('MESSAGE', '')}")
    return payload.get("row") or []


def _board_id(row: dict) -> str:
    """`BOARD_ID` 가 `464874.0` 처럼 실수로 온다 (json 타입 캐스팅). 정수 문자열로 고정한다."""
    return str(int(float(row.get("BOARD_ID"))))


def _ymd(raw: object) -> str | None:
    d = re.sub(r"\D", "", str(raw or ""))
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else None


class SeoulNoticeApi(Source):
    id = "seoul-notice-api"
    domain = "subsidy"
    category = "policy"
    subcategory = "subsidy"                   # 근거(조례·law)가 아니라 집행·모집(official)이다
    source_type = "api"
    format = "json"
    trust_level = "official"
    license = "공공누리 제1유형"
    fingerprint = "text"                      # 위 ⚠ — 응답의 list_total_count 가 매일 바뀐다

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        if not config.SEOUL_OPEN_DATA_KEY:
            raise RuntimeError(KEY_MISSING)

        res = fetcher.get(_url(1, WINDOW))
        if not res.ok:
            raise RuntimeError(f"목록 HTTP {res.status}: {config.redact(res.url)}")
        rows = _rows(res, what="목록")
        if not rows:
            raise RuntimeError("목록이 비었다 — 12,133건이던 서비스라 0건이면 서비스가 바뀐 것이다.")

        # 정렬 전제를 매번 확인한다. 깨지면 {i}/{i} 슬라이스가 엉뚱한 글을 가리킨다.
        ids = [int(_board_id(r)) for r in rows]
        if ids != sorted(ids, reverse=True):
            raise RuntimeError("목록이 BOARD_ID 내림차순이 아니다 — 인덱스 슬라이스 전제가 깨졌다.")

        targets: list[Target] = []
        for index, row in enumerate(rows, start=1):
            hit = matches(f"{row.get('TITLE', '')}\n{row.get('CONTENTS', '')}")
            if not hit:
                continue
            bid = _board_id(row)
            targets.append(Target(
                url=_url(index, index),
                slug=f"{self.id}-{bid}",
                ext="json",
                meta={
                    "title": row.get("TITLE"),
                    "board_id": bid,
                    "index": index,
                    "published_at": _ymd(row.get("CREATE_DATE")),
                    "deadline": _ymd(row.get("DEADLINE_DATE")),
                    "organ": row.get("ORGAN"),
                    "citation_url": f"{NOTICE_PAGE}/{bid}",
                    "matched_by": hit,
                },
            ))
        return targets

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        rows = _rows(res, what=f"공고 {target.meta.get('board_id')}")
        if len(rows) != 1:
            raise RuntimeError(f"{{i}}/{{i}} 슬라이스가 {len(rows)}행을 줬다 — 한 행이어야 한다.")
        row = rows[0]

        got = _board_id(row)
        want = target.meta.get("board_id")
        if got != want:
            # 새 글이 올라와 인덱스가 밀린 것. 조용히 다른 공고를 저장하는 것이 최악이라 여기서 죽는다.
            raise RuntimeError(
                f"BOARD_ID 가 다르다: 요청 {want} → 응답 {got}. discover 뒤에 새 글이 올라와 "
                "인덱스가 밀렸다. 다시 돌리면 된다.")

        title = row.get("TITLE") or target.meta.get("title") or ""
        text = "\n".join(f"{label}: {value}" for label, value in (
            ("제목", title),
            ("담당기관", row.get("ORGAN")),
            ("전화번호", row.get("TEL")),
            ("등록일", _ymd(row.get("CREATE_DATE"))),
            ("게시기간", f"{_ymd(row.get('START_DATE'))} ~ {_ymd(row.get('END_DATE'))}"),
            ("공고마감일", _ymd(row.get("DEADLINE_DATE"))),
            ("내용", " ".join(str(row.get("CONTENTS") or "").split())),
        ) if value and value != "None ~ None")

        files = [row.get(f"FILE_URL{i}") for i in range(1, 6)]
        return Extracted(
            title=title, text=text, published_at=_ymd(row.get("CREATE_DATE")),
            extra={
                "board_id": got,
                "organ": row.get("ORGAN"),
                "deadline": _ymd(row.get("DEADLINE_DATE")),
                "citation_url": target.meta.get("citation_url"),
                # 첨부는 robots 가 막아 받지 않는다. 주소만 남긴다 — 사람이 열어 볼 자리.
                "attachments": [f for f in files if f],
                "matched_by": target.meta.get("matched_by"),
            },
        )


__all__ = ["KEYWORDS", "SERVICE", "WINDOW", "SeoulNoticeApi"]
