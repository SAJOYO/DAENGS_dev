"""SRT(에스알) 반려동물 동반탑승 안내.

수서고속철도의 동반탑승 **기준 수치**를 갖는 유일한 원문이다 — 이동장 45×30×25cm, 이동장 포함
10kg, 광견병 예방접종 요건, 장애인보조견 예외. 코레일 약관(PDF)이 파서 카드(#49)에 묶여 있어
철도 축에서 먼저 답할 수 있는 소스가 이것뿐이다.

정찰 (2026-08-28, 이 카드에서 실측):
  - ⚠️ **본문이 텍스트가 아니라 이미지 한 장이고, 내용 전부가 그 `<img alt>` 안에 있다.**
    시드 노트의 `본문 .sub_con_area` 는 제목 17자짜리 껍데기다 (RAG-036 ②).
    `alt` 를 안 읽으면 "받았는데 내용이 없는" 문서가 조용히 적재된다
  - 그래서 `Extracted.text` 는 블록 텍스트 + 이미지 `alt` 를 합쳐 만든다. `alt` 가 하나도 없으면
    **경고를 남긴다** — 페이지가 진짜 텍스트로 바뀌었거나(좋음) 이미지가 교체된 것(나쁨)이라
    사람이 한 번 봐야 한다
  - 원문에 오기가 있다: `시각,청각,지체장애인 보저견` (보조견). **고치지 않고 그대로 둔다** —
    코퍼스는 원문 재현이 원칙이고, 대신 렉시컬 검색이 "보조견" 으로는 이 문장을 못 집는다
  - 약관 PDF 는 `JBCMS.downloadAttach('TK0402090000', '1')` 뒤에 있다. 이 페이지의 안내문 PDF 도
    같은 함수를 쓴다 — 첫 인자가 pageId, 둘째가 첨부 순번이다 (#40 에서 못 찾았던 파라미터).
    내용은 이미 `alt` 로 얻으므로 이 카드에서는 PDF 를 받지 않는다
  - 페이지에 공공누리 표기가 없어 `license` 를 비운다
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from ...core import textutil
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

URL = "https://etk.srail.kr/cms/archive.do?pageId=TK0402090000"
CONTAINER = ".sub_con_area"


def guide_text(box) -> tuple[str, int]:
    """본문 텍스트 + 이미지 `alt`. (텍스트, alt 개수) 를 돌려준다."""
    parts = [textutil.block_text(box)]
    n_alt = 0
    for img in box.find_all("img"):
        alt = (img.get("alt") or "").strip()
        if not alt:
            continue
        n_alt += 1
        parts.append(alt)
    return textutil.squeeze("\n".join(p for p in parts if p.strip())), n_alt


class SrtTerms(Source):
    id = "srt-terms"
    domain = "transport"
    category = "travel"
    subcategory = "transport-rail"
    source_type = "web"
    format = "html"
    trust_level = "official"
    license = ""                        # 페이지에 공공누리 표기 없음

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        return [Target(url=URL, slug=f"{self.id}-pet", ext="html",
                       meta={"title": "SRT 반려동물 동반탑승 안내"})]

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        soup = BeautifulSoup(res.content, "lxml")

        box = soup.select_one(CONTAINER)
        if box is None:
            return Extracted(title=target.meta.get("title", ""), text="",
                             extra={"warning": f"본문 컨테이너({CONTAINER}) 없음"})

        h3 = box.find(["h2", "h3", "h4"])
        title = (h3.get_text(" ", strip=True) if h3 else "") or target.meta.get("title", "")

        for t in box.select("script, style"):
            t.decompose()
        text, n_alt = guide_text(box)
        extra = {} if n_alt else {"warning": "이미지 alt 없음 — 본문이 통째로 빠졌을 수 있다"}
        return Extracted(title=title, text=text, cites=textutil.cites(text), extra=extra)
