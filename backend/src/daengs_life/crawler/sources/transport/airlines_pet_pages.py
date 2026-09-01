"""항공사 반려동물 동반 안내 — **에어프레미아 · 이스타항공.**

동반이동 도메인에서 철도는 셋(코레일 · 서울교통공사 · SRT)이 덮였지만 **항공은 0건**이었다.
이 소스가 그 층이다. 무게 한도 · 케이지 규격 · 편당 마리 수 · 요금이 전부 여기에만 있다.

────────────────────────────────────────────────────────────────────────────
정찰 (2026-08-29, 이 카드에서 실측) — **9곳 중 2곳뿐이다**
────────────────────────────────────────────────────────────────────────────
`#40` 이 8/28 에 "5곳 열림"으로 적었지만 그것은 **robots.txt 만 본 값**이었다. 실제로 문서
페이지를 받아 보니 셋이 더 막혀 있다:

  에어프레미아  ✅ 200                      이 소스
  이스타        ✅ 200                      이 소스
  에어서울      ❌ 403 `Just a moment...`   Cloudflare 챌린지
  진에어        ❌ 403 `Just a moment...`   Cloudflare 챌린지
  에어부산      ❌ TLS 핸드셰이크 거부       UA 를 빼도 같다
  대한항공·아시아나 ❌ ReadTimeout / 제주항공·티웨이 ❌ robots.txt 403

**`robots.txt` 는 정적 파일이라 CDN 이 챌린지 없이 내주고, 챌린지는 본문 페이지에서만 걸린다.**
그래서 robots 만 보면 "열려 있다"가 나온다 (`docs/life/data-sources.md` §12). 에어서울은 첫 요청이
200 이었다가 몇 요청 뒤 403 이 됐다 — **빈도로 켜진다.** 셋 다 우회하지 않는다.

**회사를 늘리는 자리는 `_ADAPTERS` 다.** 막힌 곳이 열리면 어댑터를 하나 더한다.

────────────────────────────────────────────────────────────────────────────
사별 구조
────────────────────────────────────────────────────────────────────────────
**에어프레미아** `/a/support/need/pet` — `sitemap.xml` 에 URL 이 그대로 있어 매번 거기서 찾는다.
한 페이지에 탭이 둘인데 **id 가 안정적**이다: `#need_pet_panel1`(반려동물) · `#need_pet_panel2`
(장애고객 보조견). 반려동물 탭만 받는다 — 두 탭을 합치면 `h2` 가 겹쳐(`이용 방법`·`이용 안내`가
각 탭에 하나씩) 청킹 단계에서 같은 제목의 청크가 둘 생긴다.

⚠️ **에어프레미아는 DOM 만 읽으면 알맹이가 통째로 빠진다.** Next.js 라 표 본문과 접힌 안내가
서버 HTML 에 없고 **i18n 페이로드(`need_pet_*` 키 97개)** 에 들어 있다. DOM 텍스트는 1,178자인데
거기에는 **요금 숫자가 하나도 없다** — `요금 안내 동북아 동남아 미주 ... 32 kg 이하 33 kg ~ 45 kg`
처럼 표의 머리만 남는다. 페이로드에는 이 카드가 필요로 하는 것이 전부 있다:

    need_pet_fare_32kg_nea_kr            KRW 130,000
    need_pet_fare_33kg_useu_kr           KRW 580,000
    need_pet_guide_1_2_onboard_dscrpt2   가로 38cm이하, 세로 22cm이하, 높이 최대 23cm(하드)/26cm(소프트)
    need_pet_guide_1_2_cargo_dscrpt1     용기를 포함하여 최대 무게가 45kg 이하
    need_pet_popup_ferocious_species     도사견, 핏불 테리어, 로트와일러, ...
    need_pet_popup_shortNosed_dog        단두종 개 목록
    need_pet_popup_USdogs_*              24년 8월 1일 미국 입국 규정

**이것이 지문(sha256)에도 들어가야 한다.** DOM 만 지문으로 삼으면 **요금이 바뀌어도 `same` 으로
끝난다** — 받았는데 개정을 못 잡는 조용한 실패다 (RAG-001 원칙 2 가 막으려던 바로 그것).
그래서 `text` 는 `DOM 텍스트 + 페이로드 값` 이다.

**이스타** `/newstar/PGWIM00004` — URL 이 `PGWIM00004` 같은 내부 코드라 sitemap 을 봐도 어느
쪽인지 모른다(29쪽 전부 코드다). 그래서 **코드를 박되 내용으로 검증**하고, 어긋나면 sitemap 을
훑어 다시 찾는다 (아래 `_eastar`).

⚠️ **이스타 표에는 한/일/중/대만/태 다섯 언어가 한 셀에 쌓여 있다.** 안 지우면 본문이 6,703자로
부풀고(한국어분은 4,395자) 렉시컬 검색이 일본어를 친다. 다행히 **클래스에 언어 접미사가 있다** —
`PNWIM00004_JP` · `_TW` · `_CN` · `_TH`. 한국어(`_KR`)는 접미사가 있고 영어는 없다.

⚠️ **영어는 지우지 않는다.** 지우고 싶어지는 모양이지만 **운송 요금이 영어 문장에만 있다** —
`1 passenger (1 pet) / KRW 30,000 per segment` · `Japan : KRW 120,000 / USD 120 / JPY 12,000`.
라틴 문자 비율로 거르는 휴리스틱을 쓰면 이 줄과 공항 코드(NRT · KIX)가 같이 날아간다.
**명시적으로 표시된 것만 지운다**는 규칙이 그래서 중요하다.

────────────────────────────────────────────────────────────────────────────
실측 (2026-08-29)
────────────────────────────────────────────────────────────────────────────
  에어프레미아  #need_pet_panel1  1,178자 · 요금표 `<table>` 1개 (동북아/동남아/미주 × 무게 2구간)
  이스타        article.wrap      4,395자 · `<table>` 1개 15행 (구분/내용 — 노선·허용동물·반입기준)
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ...core import nextpayload, textutil
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

# 지울 언어. 한국어(`_KR`)와 영어(접미사 없음)는 남긴다 (위 ⚠️).
# 지우는 것 자체는 `textutil.drop_class_suffix` 가 한다 — **파서도 같은 함수를 쓴다.**
FOREIGN_SUFFIXES = ("JP", "CN", "TW", "TH", "VN")

# 에어프레미아 i18n 페이로드에서 읽을 키 접두사. 읽는 것은 `core.nextpayload` 가 한다
PAYLOAD_PREFIX = "need_pet"


# --------------------------------------------------------------- 에어프레미아
AIRPREMIA = "https://www.airpremia.com"
AIRPREMIA_SITEMAP = f"{AIRPREMIA}/sitemap.xml"
# 반려동물 탭. `#need_pet_panel2` 는 장애고객 보조견이라 받지 않는다 (위 구조 메모)
AIRPREMIA_PANEL = "#need_pet_panel1"
# 실측 97개. 크게 모자라면 페이로드 규격이 바뀐 것이라 요금·규격이 빠진다
AIRPREMIA_MIN_PAYLOAD = 60


def _airpremia(fetcher: Fetcher) -> dict:
    """sitemap 에서 반려동물 페이지를 찾는다. 딥링크가 바뀌어도 따라간다 (§5 설계)."""
    res = fetcher.get(AIRPREMIA_SITEMAP)
    if not res.ok:
        raise RuntimeError(f"에어프레미아 sitemap 실패: HTTP {res.status} {AIRPREMIA_SITEMAP}")
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", res.content.decode("utf-8", "replace"))
    hits = [u for u in locs if u.rstrip("/").endswith("/pet")]
    if not hits:
        raise RuntimeError(
            f"에어프레미아 sitemap({len(locs)}개)에 반려동물 페이지가 없다 — URL 규칙이 바뀌었다")
    return {"url": hits[0], "container": AIRPREMIA_PANEL, "payload": True,
            "title": "에어프레미아 반려동물 동반 손님"}


# ------------------------------------------------------------------- 이스타
EASTAR = "https://www.eastarjet.com"
EASTAR_SITEMAP = f"{EASTAR}/sitemap.xml"
EASTAR_PAGE = f"{EASTAR}/newstar/PGWIM00004"
EASTAR_CONTAINER = "article.wrap"
# 이 문자열이 없으면 그 페이지는 더 이상 반려동물 안내가 아니다
EASTAR_MARK = "반려동물"
# 실측 4,395자. 절반 밑으로 떨어지면 페이지가 껍데기가 된 것이다
EASTAR_MIN_CHARS = 2000


def _eastar(fetcher: Fetcher) -> dict:
    """코드를 박되 **내용으로 검증**하고, 어긋나면 sitemap 을 훑어 다시 찾는다.

    URL 을 시드에 고정하면 개편 뒤에도 옛 페이지를 계속 받는다(`insurer_terms_pdfs` 가 삼성
    PDF 에서 겪은 것과 같은 함정). 그렇다고 매번 29쪽을 훑으면 한 번 수집에 요청이 29번 는다 —
    요청 간격 1.5초라 그것만 45초다. **평소 1요청, 어긋난 날만 탐색**이 그 사이다.
    """
    if _looks_like_pet_page(fetcher, EASTAR_PAGE):
        return {"url": EASTAR_PAGE, "container": EASTAR_CONTAINER, "payload": False,
                "title": "이스타항공 반려동물을 동반하는 고객"}

    res = fetcher.get(EASTAR_SITEMAP)
    if not res.ok:
        raise RuntimeError(f"이스타 sitemap 실패: HTTP {res.status} — 고정 URL 도 어긋났다")
    locs = [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>",
                                  res.content.decode("utf-8", "replace"))
            if "/newstar/" in u and u != EASTAR_PAGE]
    for url in locs:
        if _looks_like_pet_page(fetcher, url):
            return {"url": url, "container": EASTAR_CONTAINER, "payload": False,
                    "title": "이스타항공 반려동물을 동반하는 고객"}
    raise RuntimeError(
        f"이스타 반려동물 페이지를 못 찾았다 — 고정 URL({EASTAR_PAGE})과 sitemap {len(locs)}쪽 모두 "
        f"'{EASTAR_MARK}' 본문이 없다")


def _looks_like_pet_page(fetcher: Fetcher, url: str) -> bool:
    """본문 컨테이너가 있고 그 안이 반려동물 안내인지. 못 받으면 False (탐색을 계속한다)."""
    try:
        res = fetcher.get(url)
    except Exception:                              # noqa: BLE001 — 한 쪽이 죽어도 탐색은 계속
        return False
    if not res.ok:
        return False
    text = _korean_text(res.content, EASTAR_CONTAINER)
    return EASTAR_MARK in text and len(text) >= EASTAR_MIN_CHARS


# ------------------------------------------------------------------- 공통
def _korean_text(html: bytes, container: str) -> str:
    """컨테이너 안의 본문. 언어 접미사가 붙은 블록만 지운다 (`FOREIGN_SUFFIXES`)."""
    soup = BeautifulSoup(html, "lxml")
    box = soup.select_one(container)
    if box is None:
        return ""
    for tag in box.select("script, style"):
        tag.decompose()
    textutil.drop_class_suffix(box, FOREIGN_SUFFIXES)
    return textutil.squeeze(textutil.block_text(box))


_ADAPTERS = {"airpremia": _airpremia, "eastar": _eastar}


class AirlinesPetPages(Source):
    id = "airlines-pet-pages"
    domain = "transport"
    category = "travel"
    subcategory = "transport-air"
    source_type = "web"
    format = "html"
    trust_level = "official"
    # 공공저작물이 아니라 항공사 저작물이다. 원본은 `data/` 에만 두고(RAG-017),
    # 서비스 표출 시 출처 표기가 필수다 (docs/life/data-sources.md §12)
    license = "항공사 저작물 — 인용 시 출처 표기"

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        targets: list[Target] = []
        for key, adapter in _ADAPTERS.items():
            found = adapter(fetcher)
            targets.append(Target(
                url=found["url"],
                slug=f"{self.id}-{key}",
                ext="html",
                meta={
                    "title": found["title"],
                    "airline_key": key,
                    # extract 가 어디를 읽을지. 사별로 다르고 **여기 한 곳에만** 적는다
                    "container": found["container"],
                    # DOM 에 없는 i18n 페이로드까지 읽을지 (에어프레미아만 True)
                    "payload": found["payload"],
                    "notes": f"{found['title']} — 2026-08-29 정찰로 확인된 2곳 중 하나",
                },
            ))
        if not targets:
            raise RuntimeError("어댑터가 하나도 대상을 못 냈다")
        return targets

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        container = target.meta["container"]
        dom_text = _korean_text(res.content, container)
        if not dom_text:
            return Extracted(
                title=target.meta.get("title", ""), text="",
                extra={"warning": f"본문 컨테이너({container}) 없음 — 페이지 구조가 바뀌었다"})

        extra: dict = {"airline_key": target.meta["airline_key"], "dom_chars": len(dom_text)}
        parts = [dom_text]

        if target.meta.get("payload"):
            values = nextpayload.values(res.content, PAYLOAD_PREFIX)
            extra["payload_keys"] = len(values)
            if len(values) < AIRPREMIA_MIN_PAYLOAD:
                # 여기서 멈추지 않고 경고만 남긴다 — DOM 만이라도 받아 두는 편이, 아무것도 못 받고
                # 옛 원본만 남는 것보다 낫다. 대신 파싱 단계가 이 경고를 보고 판단한다
                extra["warning"] = (
                    f"i18n 페이로드가 {len(values)}개뿐이다 (실측 97). 요금·규격이 빠졌을 수 있다 — "
                    "Next.js 페이로드 규격이 바뀌었는지 확인할 것")
            parts.append("\n".join(values))

        text = textutil.squeeze("\n".join(p for p in parts if p.strip()))

        # **제목은 어댑터가 준 것을 쓴다.** 페이지의 머리글을 쓰면 에어프레미아는 `h1` 이 패널
        # 밖이라 첫 `h2`("운송 가능한 동물")가 잡히고, 이스타는 같은 `h3` 가 두 번 나온다.
        # 실제 머리글은 `extra.page_heading` 으로 남겨 바뀌면 보이게 한다.
        soup = BeautifulSoup(res.content, "lxml")
        box = soup.select_one(container)
        heading = box.find(["h1", "h2", "h3"]) if box else None
        extra["page_heading"] = heading.get_text(" ", strip=True) if heading else ""

        n_tables = len(box.find_all("table")) if box else 0
        extra["tables"] = n_tables
        if not n_tables and not target.meta.get("payload"):
            # 이스타는 수치가 전부 표에 있다. 표가 사라지면 받아도 알맹이가 없는 문서가 된다
            extra["warning"] = "표가 없다 — 무게·요금 수치가 통째로 빠졌을 수 있다"

        return Extracted(title=target.meta["title"], text=text,
                         cites=textutil.cites(text), extra=extra)
