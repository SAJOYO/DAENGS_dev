"""국립축산과학원 반려동물 포털 — 행정/법률 정보와 사육 기본사항.

정찰 (2026-08-27):
  - **시드에 있던 `/companion/` 은 죽었다.** 200 을 주면서 '페이지를 찾을 수 없습니다' 인
    soft-404 다. 진입점은 `/companion/index.do` 다
  - 정적 HTML, UTF-8. 본문 `section#contents`, 제목 `h2.pageTitle`
  - 본문 페이지는 `new_petBoard.do?cmCode=...` 30여 장이고, 좌측 lnb 에 묶음별로 나열된다
  - **조문 인용이 본문에 그대로 들어 있다** (「동물보호법」 제15조 1항) → cites 가 잘 나온다
  - 발행일 표시가 없다. published_at 은 비운다
  - `/front/search.do` 는 robots 가 막는다 (본문 경로는 허용)

**이 카드에서는 등록·법률 계열만 받는다.** `함께 외출하기`·`함께 여행가기` 는 travel 도메인이라
운송약관 카드와 같이 가는 편이 맞고, 건강·미용은 이 프로젝트의 코퍼스 범위가 아니다.
받을 것을 cmCode 가 아니라 **메뉴 제목**으로 고르는 이유는, cmCode 가 사이트 개편 때 바뀌어도
제목은 남기 때문이다. 제목이 하나도 안 맞으면 조용히 0건이 되지 않게 예외를 낸다.
"""
from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ...core import textutil
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

BASE = "https://www.nias.go.kr"

# 메뉴 제목 → (slug 꼬리, subcategory). subcategory 는 data/README.md 값 사전을 따른다.
WANTED: dict[str, tuple[str, str]] = {
    "반려동물등록제": ("registration", "registration"),
    "분실 및 유기": ("lost-stray", "registration"),
    "동물보호법": ("animal-protection-act", "pet-life-guide"),
    "반려동물의 의미": ("meaning", "pet-life-guide"),
    "반려동물 관리 책임": ("owner-duty", "pet-life-guide"),
    "동물학대 금지": ("abuse-ban", "pet-life-guide"),
    "사육에 관한 기본사항": ("care-basics", "pet-life-guide"),
}


class NiasPet(Source):
    id = "nias-pet"
    domain = "registration"
    category = "policy"
    subcategory = "pet-life-guide"      # 문서마다 Target.meta 로 덮어쓴다
    source_type = "web"
    format = "html"
    trust_level = "official"
    license = "공공누리 제1유형"

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        res = fetcher.get(self.seed["url"])
        if not res.ok:
            raise RuntimeError(f"seed page HTTP {res.status}: {self.seed['url']}")
        soup = BeautifulSoup(res.content, "lxml")

        found: dict[str, str] = {}
        for a in soup.select('a[href*="new_petBoard.do"]'):
            label = a.get_text(" ", strip=True)
            if label in WANTED:
                found.setdefault(label, urljoin(BASE, a["href"]))

        missing = [t for t in WANTED if t not in found]
        if not found:
            raise RuntimeError("메뉴에서 본문 링크를 하나도 찾지 못함 — 페이지 구조가 바뀐 듯")

        targets = []
        for label, (tail, subcategory) in WANTED.items():
            if label not in found:
                continue
            targets.append(Target(
                url=found[label], slug=f"{self.id}-{tail}", ext="html",
                meta={"title": label, "subcategory": subcategory,
                      "notes": f"메뉴: {label}" + (f" (못 찾은 메뉴: {', '.join(missing)})" if missing else "")},
            ))
        return targets

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "lxml")

        box = soup.select_one("section#contents")
        if box is None:
            return Extracted(title=target.meta.get("title", ""), text="",
                             extra={"warning": "본문 컨테이너 없음"})

        h2 = box.select_one("h2.pageTitle")
        title = h2.get_text(" ", strip=True) if h2 else target.meta.get("title", "")

        for t in box.select("script, style, .tabscontents"):
            t.decompose()
        text = textutil.squeeze(textutil.block_text(box))
        return Extracted(title=title, text=text, cites=textutil.cites(text))
