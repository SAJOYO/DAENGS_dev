"""법제처 찾기쉬운 생활법령정보 — "반려동물과 생활하기" (csmSeq=1809).

정찰 (2026-08-19):
  - 정적 HTML, UTF-8. 본문은 #ovDiv .ovDivbox, 100문100답 탭은 .ovDivbox1
  - 좌측 트리에 본문 7페이지 (CnpClsMain.laf?csmSeq=1809&ccfNo=..&cciNo=..&cnpClsNo=..)
  - 각 본문 페이지에 &menuType=onhunqna 를 붙이면 같은 주제의 100문100답(Q/A + 관련법령) 이 나온다
  - 카드뉴스(EasyLawInfoR) 는 이미지라 텍스트가 없어 제외
  - 하단 info_box 에 "이 정보는 YYYY년 M월 D일 기준으로 작성" → published_at
  - 본문이 「동물보호법」 제16조 제2항 식으로 조항을 인라인 인용 → cites
  - 조문 인용이 <a> 로 감싸여 있어 get_text("\\n") 을 쓰면 "제16조" 와 "제2항" 이 다른 줄로 쪼개진다
    → 블록 태그 경계에서만 줄바꿈하는 _block_text() 사용
"""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from ...core import textutil
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

BASE = "https://www.easylaw.go.kr"
CSM_SEQ = "1809"

_RE_PUBLISHED = re.compile(r"이 정보는\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*기준")

_NOISE = [
    re.compile(r"인쇄체크"),
    re.compile(r"^문화/여가생활\s*:\s*반려동물과 생활하기\s*:\s*", re.M),      # 100문100답 머리말
    re.compile(r"\s*조회수\s*:\s*[\d,]+건\s*추천수\s*:\s*[\d,]+건"),
    re.compile(r"\n새소식 상세 내용(?=\n)"),
    re.compile(r"\(\s*\n"), re.compile(r"\n\s*\)"),                            # 괄호 안 줄바꿈 붙이기
]


class EasylawPet(Source):
    id = "easylaw-pet"
    domain = "law"
    category = "policy"
    subcategory = "pet-life-guide"          # 등록·외출·사육·장례 종합 해설. 청킹 단계에서 페이지별로 세분 가능
    source_type = "web"
    format = "html"
    trust_level = "official"                # 해설은 official, 인용된 조문 자체는 law (cites 로 보존)
    license = "공공누리 제1유형"

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        res = fetcher.get(self.seed["url"])
        if not res.ok:
            raise RuntimeError(f"seed page HTTP {res.status}: {self.seed['url']}")
        soup = BeautifulSoup(res.content, "lxml")

        seen: dict[tuple[str, str, str], str] = {}
        for a in soup.select('a[href*="CnpClsMain.laf"]'):
            href = html.unescape(a.get("href", ""))
            q = parse_qs(urlsplit(href).query)
            if q.get("csmSeq", [""])[0] != CSM_SEQ or "cnpClsNo" not in q:
                continue
            key = (q["ccfNo"][0], q["cciNo"][0], q["cnpClsNo"][0])
            seen.setdefault(key, urljoin(BASE, href))

        if not seen:
            raise RuntimeError("좌측 트리에서 본문 링크를 찾지 못함 — 페이지 구조가 바뀌었을 수 있음")

        targets: list[Target] = []
        for (ccf, cci, cnp), url in sorted(seen.items(), key=lambda kv: tuple(int(x) for x in kv[0])):
            base_slug = f"easylaw-pet-{ccf}-{cci}-{cnp}"
            clean = self._clean_url(url)
            targets.append(Target(url=clean, slug=base_slug, ext="html", meta={"kind": "body"}))
            targets.append(Target(url=clean + "&menuType=onhunqna", slug=base_slug + "-qna",
                                  ext="html", meta={"kind": "qna"}))
        return targets

    @staticmethod
    def _clean_url(url: str) -> str:
        """popMenu=ov 같은 UI 파라미터 제거, 파라미터 순서 고정 → slug/URL 안정화."""
        q = parse_qs(urlsplit(url).query)
        return (f"{BASE}/CSP/CnpClsMain.laf?csmSeq={q['csmSeq'][0]}"
                f"&ccfNo={q['ccfNo'][0]}&cciNo={q['cciNo'][0]}&cnpClsNo={q['cnpClsNo'][0]}")

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "lxml")

        title_tag = soup.find("title")
        title = (title_tag.get_text(" ", strip=True) if title_tag else "").split("|")[0].strip()

        # published_at 은 info_box 에 있으므로 제거 전에 페이지 전체에서 먼저 읽는다
        published = None
        m = _RE_PUBLISHED.search(soup.get_text(" ", strip=True))
        if m:
            published = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

        box = (soup.select_one("#ovDiv .ovDivbox") if target.meta.get("kind") == "body"
               else soup.select_one(".ovDivbox1"))
        if box is None:
            return Extracted(title=title, text="", published_at=published,
                             extra={"warning": "본문 컨테이너 없음"})

        for t in box.select("script, style, .copyImgAddress, .info_box"):
            t.decompose()
        text = self._block_text(box)
        return Extracted(title=title, text=text, published_at=published, cites=textutil.cites(text))

    @staticmethod
    def _block_text(box) -> str:
        """공통 블록 추출 + 이 사이트 고유 노이즈 제거."""
        text = textutil.block_text(box)
        for pat in _NOISE:
            text = pat.sub(lambda mm: "(" if mm.group(0).lstrip().startswith("(") else
                           (")" if mm.group(0).rstrip().endswith(")") else ""), text)
        return textutil.squeeze(text)
