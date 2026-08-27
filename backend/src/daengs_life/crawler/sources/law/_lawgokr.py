"""국가법령정보센터(law.go.kr) 웹 원문 공통 베이스.

`law-animal-protection` 과 `law-livestock-epidemic` 은 받는 법령만 다르고 사이트가 같다.
소스 모듈은 LAWS(법령명 목록)만 선언하고 여기가 나머지를 한다.

정찰 (2026-08-20):
  - `https://www.law.go.kr/법령/{법령명}` 은 1.2KB 짜리 껍데기다. 본문은 iframe#lawService 안에 있고
    그 src 에 lsiSeq(법령ID)와 efYd(시행일자)가 박혀 있다 → 이 둘을 뽑아 실제 대상 URL 을 만든다
  - iframe 이 가리키는 `lsInfoP.do` 는 조문을 AJAX 로 채워 본문이 1.5천 자뿐이다.
    같은 파라미터로 **`lsInfoR.do` 를 부르면 조문이 서버 렌더링되어 통째로 나온다** (6만~11만 자)
  - `#conTop` : "동물보호법 [시행 2026. 7. 7.] [법률 제21839호, 2026. 7. 7., 일부개정]"
    → 법령명·시행일자·공포번호·개정구분. 시행일자를 published_at 으로 쓴다
  - `#conScroll` : 본문. 조 하나 = `div.lawcon` 하나 (동물보호법 103개)
    항은 p.pty1_de2_1, 호는 p.pty1_de2h, 목은 p.pty1_de3 → RAG-004 조문 단위 청킹의 경계가 그대로 있다
  - 조문 앞에 붙는 <input type=checkbox> 는 UI 라 제거한다 (남기면 지문에 섞인다)

lsiSeq 는 개정마다 새로 발급된다. URL 을 시드에 고정하지 않고 매번 껍데기에서 읽는 이유 —
고정하면 개정 후에도 옛 버전만 계속 받게 되고, 이 도메인은 "최신 시행본" 이 유일하게 맞는 답이다.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from ...core import textutil
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

BASE = "https://www.law.go.kr"

_RE_EFYD = re.compile(r"\[시행\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*\]")
# [법률 제21839호, 2026. 7. 7., 일부개정]
_RE_PROM = re.compile(r"\[([^\[\],]+?)\s*제(\d+)호,\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?,\s*([^\]]+)\]")


class LawGoKrSource(Source):
    """law.go.kr 웹 원문 소스의 공통 구현. 하위 클래스는 LAWS 와 클래스 속성만 채운다."""

    domain = "law"
    category = "policy"
    source_type = "web"
    format = "html"
    trust_level = "law"
    license = "공공누리 제1유형"

    LAWS: list[tuple[str, str]] = []          # (법령명, slug 접미사)

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        targets: list[Target] = []
        for name, suffix in self.LAWS:
            shell_url = f"{BASE}/법령/{name.replace(' ', '')}"
            res = fetcher.get(shell_url)
            if not res.ok:
                raise RuntimeError(f"껍데기 페이지 HTTP {res.status}: {shell_url}")

            iframe = BeautifulSoup(res.content, "lxml").select_one("iframe#lawService")
            if iframe is None or not iframe.get("src"):
                raise RuntimeError(f"iframe#lawService 없음 — 페이지 구조가 바뀌었을 수 있음: {shell_url}")

            q = parse_qs(urlsplit(iframe["src"]).query)
            lsi_seq, ef_yd = q.get("lsiSeq", [""])[0], q.get("efYd", [""])[0]
            if not lsi_seq:
                raise RuntimeError(f"iframe src 에 lsiSeq 없음: {iframe['src']}")

            targets.append(Target(
                url=f"{BASE}/LSW/lsInfoR.do?lsiSeq={lsi_seq}&efYd={ef_yd}",
                slug=f"{self.id}-{suffix}",
                ext="html",
                meta={"title": name, "lsiSeq": lsi_seq, "efYd": ef_yd, "shell_url": shell_url},
            ))
        return targets

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "lxml")

        top = soup.select_one("#conTop")
        top_text = textutil.WS.sub(" ", top.get_text(" ", strip=True)) if top else ""
        title = top_text.split("[")[0].strip() or target.meta.get("title", "")

        published = None
        if m := _RE_EFYD.search(top_text):
            published = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

        extra: dict[str, object] = {"lsiSeq": target.meta.get("lsiSeq")}
        if m := _RE_PROM.search(top_text):
            extra["promulgation"] = f"{m.group(1)} 제{m.group(2)}호"
            extra["promulgated_at"] = f"{m.group(3)}-{int(m.group(4)):02d}-{int(m.group(5)):02d}"
            extra["revision_kind"] = m.group(6).strip()

        body = soup.select_one("#conScroll")
        if body is None:
            return Extracted(title=title, text="", published_at=published,
                             extra={**extra, "warning": "#conScroll 없음"})

        articles = [a.get_text(" ", strip=True)
                    for p in body.select("div.lawcon p.pty1_p4")
                    if (a := p.select_one("span.bl label"))]
        extra["articles"] = len(articles)
        extra["first_article"] = articles[0] if articles else None

        # 제거 대상 (전부 UI 이거나 본문과 무관해 지문만 흔든다):
        #   .cont_subtit  #conScroll 첫 자식인 소관부처 연락처 412자. 조직개편·전화번호만 바뀌어도
        #                 법령이 개정된 것처럼 보인다
        #   .byl_pop      별표·서식 파일 저장 레이어. 같은 제목을 .hwp/.hwpx/.pdf 로 4번 반복하고
        #                 "닫힘", "아래 을 눌러 주소를 복사하세요" 가 섞인다.
        #                 별표 목록 자체(#arDivArea)는 남긴다 — 어떤 별표가 있는지는 본문 정보다
        #   .rule_area    위임행정규칙 안내 박스
        for t in body.select("script, style, input, .cont_subtit, .byl_pop, .rule_area"):
            t.decompose()
        text = textutil.squeeze(textutil.block_text(body))
        return Extracted(title=title, text=text, published_at=published,
                         cites=textutil.cites(text), extra=extra)
