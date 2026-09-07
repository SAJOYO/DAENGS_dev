"""국립축산과학원 반려동물 포털 파서 — 행정/법률 해설 + 음식·사료.

정찰 (2026-08-27):
  - 정적 HTML, UTF-8. 본문 `section#contents`, 문서 제목 `h2.pageTitle`, 절 제목 `h3.subtit`
  - 절 안은 `ul.inDiv1 > li` 이고 `<strong>` 가 항목 이름이다 (인라인이라 문단에 그대로 남는다)
  - `.tabscontents` 는 탭 UI 잔여물이라 뺀다
  - 발행일 표시가 없다

**조문 인용이 본문에 그대로 있다** (「동물보호법」 제15조 1항). `textutil.cites` 가 문단마다
뽑아 주므로 해설 청크가 어느 조문에 근거하는지 남는다 — easylaw 와 같은 성격이다.

────────────────────────────────────────────────────────────────────────────
2026-09-06 (RAG-065 / F1) — 탭 페이지에서 **먹이 두 절만** 남긴다
────────────────────────────────────────────────────────────────────────────
`반려견 건강상식` · `반려묘 건강상식` 은 `div.tab_container > div.tab_content#tabN` 여섯 장이
한 HTML 에 다 들어 있는 구조다. 그중 우리가 쓰는 것은 **`올바른 먹이 선택 가이드`(tab4)와
`급여해서는 안되는 음식`(tab6)** 뿐이고, 나머지 넷(운동·식사량, 예방접종, 계절별 돌보는 법,
수명 연령표)은 `roadmap.md` §5 가 🚫 한 **"`care`·건강 상식 확장"**(RAG-008 ③)이다.

⚠ **탭은 서버가 아니라 화면이 가른다.** `cmCode` 네 개(`…816`·`…288`·`…142`·`…599`)가
  **전부 같은 바이트**를 돌려준다. 그래서 탭마다 타깃을 잡으면 같은 파일이 네 벌 쌓인다 —
  페이지는 한 장으로 받고 **여기서 자른다.**

⚠ **id(`#tab6`)가 아니라 절 제목으로 고른다.** 사이트가 탭 순서를 바꾸면 id 는 움직이지만
  `h3.subtit` 의 글자는 남는다. `crawler/sources/.../nias_pet.py` 가 cmCode 대신 메뉴 제목으로
  고르는 것과 같은 이유다.

**증상 문장은 자른다** (2026-09-06 사람 결정). 아래 `_SYMPTOM` 참고.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Heading
from ...extract import prose
from ..base import Parsed

NAME = "nias_html"
VERSION = 2                             # 2026-09-06 절 선별 + 증상 절단 (RAG-065)

CONTAINER = "section#contents"
HEADINGS = ("h3",)

# 탭 페이지에서 **남길** 절. 위 도크스트링 참고 — 나머지는 §5 의 🚫 건강 상식이다.
KEEP_SECTIONS = frozenset({"올바른 먹이 선택 가이드", "급여해서는 안되는 음식"})

# 증상 어휘. 이 중 하나라도 든 **문장**을 통째로 뺀다 (2026-09-06 사람 결정 — "넣되 증상은 자른다").
#
# 왜 문장 단위인가 — 절 단위로 자르면 "…테오브로민이라는 것인데" 처럼 **연결어미로 끝나는 조각**이
# 남는다. 청크는 생성기가 그대로 인용하는 재료라 조각을 남기면 답변에 조각이 나온다.
# 문장을 통째로 빼면 남는 것은 언제나 완전한 문장이다.
#
# ⚠ **한 항목의 본문이 통째로 비는 것은 정상이다.** `포도, 건포도` 가 그렇다 — 두 문장이 다
#   증상이다. 그래도 절 제목(`급여해서는 안되는 음식`)과 항목 이름(`포도, 건포도`)은 남으므로
#   "포도 먹여도 되나요"에는 답한다. **이름이 곧 답인 문서다.**
#   빈 항목을 채우려고 문장을 지어내지 않는다.
_SYMPTOM = re.compile(
    r"구토|설사|경련|발작|부정맥|빈혈|신부전|천공|호흡곤란|복통|췌장염|염증|중독|"
    r"무기력|식욕감퇴|식욕저하|기력저하|체온상승|심장박동|출혈|근육약화|보행이상|"
    r"증세|증상|목숨|죽음|생명"
)
# 한국어 문장 끝. `습니다.` / `합니다.` / `됩니다.` 뒤에서 자른다.
_SENTENCE_END = re.compile(r"(?<=다\.)\s*")


def cut_symptoms(text: str) -> str:
    """증상 어휘가 든 문장을 뺀 나머지. 남는 문장이 없으면 빈 문자열이다."""
    kept = [s for s in _SENTENCE_END.split(text) if s.strip() and not _SYMPTOM.search(s)]
    return " ".join(s.strip() for s in kept)


def _select_sections(box) -> None:
    """탭 페이지면 `KEEP_SECTIONS` 밖의 탭을 지운다. 탭이 없으면 아무것도 안 한다.

    하나도 안 남으면 **예외를 낸다** — 절 제목이 바뀌었는데 조용히 빈 문서를 적재하면
    "받았는데 답을 못 한다"로만 보이고 원인이 안 보인다 (크롤러 discover 와 같은 판단).
    """
    tabs = box.select("div.tab_content")
    if not tabs:
        return
    kept = 0
    for tab in tabs:
        titles = {h.get_text(" ", strip=True) for h in tab.select("h3.subtit")}
        if titles & KEEP_SECTIONS:
            kept += 1
        else:
            tab.decompose()
    if not kept:
        raise RuntimeError(
            f"탭 {len(tabs)}장 중 남길 절이 하나도 없다 — 절 제목이 바뀌었다. "
            f"기대: {sorted(KEEP_SECTIONS)}"
        )


def _cut_symptom_sentences(box) -> None:
    """`급여해서는 안되는 음식` 절의 설명(`dd`)에서 증상 문장을 뺀다.

    항목 이름은 `dt` 라 손대지 않는다. `dd` 가 통째로 비면 태그째 지운다 — 빈 문단이
    청크로 남으면 검색에 잡히고도 아무것도 말하지 않는다.
    """
    for tab in box.select("div.tab_content"):
        titles = {h.get_text(" ", strip=True) for h in tab.select("h3.subtit")}
        if "급여해서는 안되는 음식" not in titles:
            continue
        for dd in tab.select("dd"):
            cut = cut_symptoms(dd.get_text(" ", strip=True))
            if cut:
                dd.string = cut
            else:
                dd.decompose()


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    soup = BeautifulSoup(raw, "lxml")

    box = soup.select_one(CONTAINER)
    if box is None:
        raise RuntimeError(f"본문 컨테이너({CONTAINER})가 없다 — 페이지 구조가 바뀌었다")

    h2 = box.select_one("h2.pageTitle")
    title = (h2.get_text(" ", strip=True) if h2 else "") or doc.meta.get("document_title", "")
    if h2 is not None:
        h2.decompose()                      # 문서 제목은 헤더로 가므로 문단으로 또 넣지 않는다

    _select_sections(box)                   # 탭 페이지면 먹이 두 절만 (RAG-065)
    _cut_symptom_sentences(box)             # 급여 금지 항목의 증상 문장 절단 (RAG-065)

    elements: list = []
    #  는 탭 **메뉴**(절 제목 네 개가 나열된 내비게이션)다. 본문이 아니라 목차라
    # 남겨 두면 "급여해서는 안되는 음식" 이라는 글자만 든 문단이 청크로 하나 더 생긴다.
    body = prose.elements(box, doc.doc_id, headings=HEADINGS,
                          noise=prose.NOISE + ", .tabscontents, #tab_box")
    if not any(e.type == "heading" for e in body):
        elements.append(Heading(id=f"{doc.doc_id}#h2-0", level=2, text=title))
    elements += body

    return Parsed(elements=elements, document_title=title,
                  citation_url=doc.meta.get("source_url"))
