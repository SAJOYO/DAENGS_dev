"""포맷 층 ③ — 약관 PDF 를 heading / article / table 로 편다 (RAG-018, RAG-032).

`boxtable.py` 가 법령 XML 안의 괘선표를, `prose.py` 가 기관 해설 HTML 을 맡는 것과 같은
자리다. 여기는 **조항이 줄 머리에 오는 약관 PDF** 를 맡는다 — 운송약관·보험약관이 그 모양이고,
사이트가 달라도 이 구조는 같아서 포맷 층으로 올린다.

⚠️ **이 모듈은 `pdf` 그룹(PyMuPDF, AGPL)에 의존한다.** 그룹이 backend 이미지에 안 깔리므로
(RAG-032 ②) **서빙 경로에서 import 되면 안 된다.** 파싱 단계에서만 부른다.

────────────────────────────────────────────────────────────────────────────
실측 (2026-08-28) — 코레일 여객운송약관 22p · 광역철도 44p
────────────────────────────────────────────────────────────────────────────
**① 조 번호가 문서 안에서 재시작한다.** 여객운송약관은 `여객운송약관`(제1장~제5장) 뒤에
`정기승차권이용에관한약관` 이 붙은 **합본**이라, 제1조가 두 번 나온다 — 조 머리 59개 중
**35개가 번호 중복**이었다. 그냥 `제N조` 를 섹션으로 쓰면 chunk_id 가 겹치고, 겹치면
골든셋 라벨이 어느 쪽을 뜻하는지 사라진다 (RAG-033 ③ 에서 조례 부칙으로 같은 일을 겪었다).
→ **약관이 바뀌면 섹션에 그 이름을 붙인다.** 첫 약관은 문서 제목과 같으므로 안 붙인다.

**② 띄어쓰기가 없다.** 추출 텍스트가 `이약관은한국철도공사가운영하는` 처럼 붙어 나온다.
PDF 에 공백 글리프가 없는, 한국어 PDF 의 흔한 성질이다.
**복원하지 않는다** — Kiwi 의 `space()` 로 넣어 봤더니 검색이 **나빠졌다**:

    원문 57토큰  `철도안전법` `위해물품` `국토교통부장관` `시행규칙` `예방접종` 살아남음
    복원 46토큰  전부 쪼개짐 (`철도 안전 법`), `예방접종` 소실

`core/tokenize.py` 가 붙어 있는 체언을 복합어로 되붙이는데(RAG-035 ③ⓐ), 공백을 넣으면
그 신호가 사라진다. **원문의 '붙어 있음' 이 오히려 복합어 표시였다.** 원본 보존 원칙과도 맞다.

**③ 표는 유효표만 취한다** (RAG-032 의 판정). 행 2 · 열 2 · 빈 셀이 절반 미만.
코레일 여객운송약관은 검출 4개 중 유효 1개였고 나머지는 레이아웃 박스다. 안 거르면
빈 격자가 그대로 청크가 된다. 광역철도는 11개가 전부 유효표였다.

**④ 스캔 PDF 는 없었다.** 세 문서 모두 0자 페이지가 0이다. 나오면 D-006 의 OCR 논의를
재개할 근거가 되므로 **경고로 남기고 멈추지 않는다** — 한 쪽이 비어도 나머지는 쓸 수 있다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from daengs_life.rag.core.ir import AnyElement, Article, Heading, Para, Table

# 줄 머리의 조. `제12조의3(제목)` 까지 받는다.
#
# ⚠️ **제목 괄호를 필수로 둔다.** 안 그러면 본문의 조 **참조**가 조 머리로 잡힌다 —
# 실측에서 `제25조에서 정한 운임과…` 라는 표 안 문장이 제25조를 한 번 더 만들어
# chunk_id 가 겹쳤다. 약관의 조는 예외 없이 `제N조(제목)` 형태라 이 조건이 안전하다.
# 줄 끝으로 끝나는 경우(제목이 다음 줄로 넘어간 판)도 받아 준다.
_RE_ARTICLE = re.compile(r"^제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:\(([^)]*)\)|$)")
# 장·절. 조와 달리 청킹 대상이 아니라 경계 표시다
_RE_DIVISION = re.compile(r"^(제\s*\d+\s*[편장절관](?:\s*의\s*\d+)?)\s*(.*)$")
# 문서 안의 약관 경계. `정기승차권이용에관한약관` 처럼 짧은 줄이 통째로 약관 이름이다.
#
# ⚠️ 띄어쓰기가 없어서(② 참고) **본문 한 줄이 통째로 약관 이름처럼 보인다.** 실측에서
# `③이약관에서정하지않은사항은연락운송을하는기관의약관` 이라는 항 본문이 걸렸다.
# 그래서 **항 번호(①②③…)나 호 번호(1. 2.)로 시작하면 제목이 아니다** — 문서의 구분 제목이
# 항 번호로 시작하는 일은 없다.
_RE_TERMS = re.compile(r"^(?![①-⑳\d])[^\s]{4,30}약관$")
# 부칙 머리. `부칙` 한 줄이거나 `부칙 <제2024-1호>` 처럼 온다.
# **부칙 안의 조는 본문 조와 번호가 겹친다** — 본문 제1조(목적)와 부칙 제1조(시행일)가 그렇다
_RE_ADDENDUM = re.compile(r"^부\s?칙(\s|<|$)")

_WS = re.compile(r"\s+")


@dataclass
class Parsed:
    """포맷 층 결과. 파서가 문서 헤더를 얹어 `parsers.base.Parsed` 로 옮긴다."""
    elements: list[AnyElement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _clean(s: str) -> str:
    return _WS.sub(" ", s).strip()


def _section(no: str, branch: str | None, terms: str | None) -> str:
    """`제15조` / `제12조의3`. 합본의 뒤쪽 약관이면 이름을 앞에 붙인다 (위 ①)."""
    base = f"제{int(no)}조" + (f"의{int(branch)}" if branch else "")
    return f"{terms} {base}" if terms else base


def _same_document(terms_line: str, title: str) -> bool:
    """첫 약관 이름이 문서 제목과 같은 것을 가리키는가.

    공백을 지우고, 뒤에 붙은 `보통약관`/`약관` 을 떼고 비교한다. 뗀 뒤가 비면(`보통약관`
    한 단어) 그것도 문서 자신을 가리키는 것으로 본다.
    """
    a = terms_line.replace(" ", "")
    b = title.replace(" ", "")
    if a == b:
        return True
    core = re.sub(r"(?:보통)?약관$", "", a)
    return not core or core in b


def _valid_table(rows: list[list[str | None]]) -> bool:
    """RAG-032 의 유효표 판정. 레이아웃 박스를 표로 오인하지 않기 위한 것."""
    if len(rows) < 2:
        return False
    if max((len(r) for r in rows), default=0) < 2:
        return False
    cells = [c for r in rows for c in r]
    if not cells:
        return False
    empty = sum(1 for c in cells if not (c or "").strip())
    return empty < len(cells) / 2


def elements(doc, doc_id: str, *, title: str = "",
             pages: range | None = None,
             terms_re: re.Pattern[str] | None = None) -> Parsed:
    """PyMuPDF `Document` → 요소 목록. **원문 순서 그대로.**

    `title` 은 문서 제목이다. 합본의 **첫 약관**은 보통 이것과 같아서, 그때는 섹션에 약관
    이름을 안 붙인다 — `여객운송약관 제1조` 대신 `제1조` 가 되어 인용이 짧아진다.

    `pages` 는 **읽을 페이지 범위**다 (0-based). `None` 이면 전부.
    `terms_re` 는 **약관 경계 정규식**을 갈아 끼운다. `None` 이면 `_RE_TERMS`.

    둘 다 선택 인자이고 기본값이 종전 동작이라 **코레일 쪽은 아무것도 안 바뀐다.** 넣은
    이유는 보험약관이다 (RAG-039):

      **⑤ 보험약관 PDF 는 한 파일에 성격이 다른 세 덩어리가 들어 있다.** 앞에 안내 책자
      (`약관이용 Guide Book`), 가운데 약관 본문, 뒤에 **관계법령 전문**(신용정보법·
      국민건강보험법·상법 …)이 붙는다. 뒤엣것을 그대로 읽으면 `제32조` 가 **"펫보험 약관
      제32조"** 로 인용되는데 실제로는 신용정보법이다 — 분량이 아니라 **틀린 인용**을 만든다.
      앞엣것은 조 **참조**가 표로 실려 있어(`제8조(보험금의 지급절차) … p.35`) 그 참조가
      조 머리로 잡히고 뒤따르는 안내문을 통째로 삼킨다 (실측 11,239자, 하드 상한 초과).
      경계 마커가 **보험사 문서의 성질**이라 포맷 층이 아니라 사이트 층이 안다. 그래서
      여기는 "어디를 읽을지" 만 받는다.

      **⑥ 보험 특별약관 이름에는 공백이 있다.** `_RE_TERMS` 는 공백 없는 4~30자를 받는데
      (코레일 `정기승차권이용에관한약관`), 보험은 `반려묘 수술비(치과및구강질환포함)
      확대보장(재가입형) 특별약관` 처럼 공백이 있고 43자다. 그대로 두면 **경계를 0개 잡고**
      조 번호가 문서 안에서 24~48번 재시작한 채 chunk_id 가 겹친다. 정규식을 여기서 넓히지
      않고 갈아 끼우게 한 이유는 **코레일이 그 넓은 규칙을 지나가지 않게** 하기 위해서다.
    """
    out = Parsed()
    seen_section: Counter[str] = Counter()   # 섹션 이름 → 몇 번째인지 (id 유일성 보장)
    seen_table: Counter[str] = Counter()     # 섹션 → 그 안의 표 번호 (쪽을 넘어가며 이어 센다)
    terms: str | None = None          # 지금 읽고 있는 약관 (첫 약관이면 None)
    seen_terms = 0
    cur: Article | None = None
    body: list[str] = []
    n_div = 0
    add_lines: list[str] = []         # 지금 모으고 있는 부칙. **제자리에서 비운다** (아래 참고)
    n_add = 0

    def flush_add() -> None:
        """부칙 하나를 `Para` 로 낸다.

        **`Article` 이 아니라 `Para` 인 것이 요점이다.** 청커가 `para` 의 section 에 '부칙'
        이 있으면 `_supplementary` 로 보내 단문 시행일·타법개정을 걸러 준다 (RAG-021 ①).
        `article` 로 내면 그 필터를 안 타고, 게다가 부칙의 `제1조(시행일)` 이 본문 `제1조` 와
        chunk_id 가 겹친다 — 조례에서 똑같이 겪었다 (RAG-033 ③).

        `add_lines = []` 로 새 리스트를 만들지 않고 `clear()` 로 비우는 이유는, 그래야
        이 클로저와 바깥 루프가 **같은 객체**를 계속 보기 때문이다.
        """
        nonlocal n_add
        if add_lines:
            n_add += 1
            out.elements.append(Para(id=f"{doc_id}#부칙-{n_add}", title=add_lines[0],
                                     text="\n".join(add_lines[1:]) or add_lines[0],
                                     section="부칙"))
        add_lines.clear()

    def flush() -> None:
        """모아 둔 줄을 지금 조에 넣는다."""
        nonlocal cur, body
        if cur is not None:
            head = "\n".join([cur.head, *body]) if body else cur.head
            out.elements.append(cur.model_copy(update={"head": head, "chars": len(head)}))
        cur, body = None, []

    terms_rx = terms_re or _RE_TERMS
    # `pages` 가 없으면 **문서를 그대로 순회한다** — `doc.page_count` 를 거치지 않는 이유는
    # 테스트가 페이지 목록만 흉내 낸 가짜 문서를 넘기기 때문이다 (`test_pdf_extract`).
    # 범위를 받았을 때만 인덱스로 집는다.
    numbered = enumerate(doc) if pages is None else ((i, doc[i]) for i in pages)

    for pno, page in numbered:
        text = page.get_text()
        if not text.strip():
            # ④ 스캔 페이지 후보. 멈추지 않고 남긴다 — 나머지 쪽은 쓸 수 있다
            out.warnings.append(f"{pno + 1}쪽에 텍스트가 없다 (스캔 페이지일 수 있다 — D-006)")
            continue

        for raw in text.split("\n"):
            line = _clean(raw)
            if not line:
                continue

            # 부칙 머리 — 여기부터 조 번호가 본문과 겹치기 시작한다
            if _RE_ADDENDUM.match(line):
                flush()
                flush_add()
                add_lines.append(line)
                continue

            # 부칙을 읽는 중이면 조도 본문이 아니라 부칙 줄이다
            if add_lines and not terms_rx.match(line):
                add_lines.append(line)
                continue

            # 약관 경계 — 조 번호가 여기서 재시작한다
            if terms_rx.match(line) and not _RE_ARTICLE.match(line):
                flush()
                flush_add()
                seen_terms += 1
                # 첫 약관이 문서 제목과 **겹치면** 접두어를 안 붙인다 (인용이 짧아진다).
                #
                # 코레일은 둘이 똑같았지만(`여객운송약관`), 보험은 제목이 상품명이고 약관 이름은
                # 그 상품명 뒤에 `보통약관` 이 붙은 꼴이라 **같지 않은데 겹친다** —
                #   제목 `무배당 삼성화재 다이렉트 착한펫보험(강아지)(2605.1)(재가입계약용)`
                #   약관 `착한펫보험(강아지)(2605.1)(재가입계약용)보통약관`
                # 그대로 두면 인용이 `…(재가입계약용) …(재가입계약용)보통약관 제25조` 가 되어
                # 상품명이 두 번 나온다. **답변에 실리는 문자열이라 눈에 띈다.**
                terms = None if (seen_terms == 1 and _same_document(line, title)) else line
                out.elements.append(Heading(id=f"{doc_id}#{line}", level=1, text=line, section=line))
                continue

            if m := _RE_ARTICLE.match(line):
                flush()
                section = _section(m.group(1), m.group(2), terms)
                # **같은 섹션이 문서 안에서 되풀이되면 순번을 붙인다.**
                #
                # 조 번호는 약관이 바뀔 때마다 재시작하고, 그 경계를 100% 잡는 것은 PDF 에서
                # 불가능에 가깝다 — 줄바꿈이 문장을 아무 데서나 끊어서 `…때에는 특별약관` 같은
                # **본문 조각이 약관 이름처럼 보인다** (RAG-039 ⑤). 경계를 놓치면 두 약관의
                # 제1조가 같은 id 를 갖는데, **그 중복은 조용하다** — 청크는 둘 다 남고
                # 골든셋 라벨만 어느 쪽을 뜻하는지 잃는다 (RAG-019 · RAG-022 ⑥B).
                #
                # 그래서 경계 검출과 **무관하게** 유일성을 보장한다. 코레일은 약관 이름 접두어로
                # 이미 겹침이 없어 이 순번이 붙지 않는다 — 붙는다면 그건 경계를 놓쳤다는 신호다.
                seen_section[section] += 1
                nth = seen_section[section]
                section = section if nth == 1 else f"{section}-{nth}"
                cur = Article(id=f"{doc_id}#{section}", section=section,
                              title=_clean(m.group(3) or "") or None, head=line,
                              paragraphs=[], chars=len(line))
                continue

            if (d := _RE_DIVISION.match(line)) and cur is None:
                # 장·절 제목. **조 안에서 나오면 본문이다** — 조문이 "제3장" 을 인용할 수 있다
                n_div += 1
                sec = d.group(1).replace(" ", "")
                out.elements.append(Heading(id=f"{doc_id}#{sec}-{n_div}", level=2,
                                            text=line, section=sec))
                continue

            if cur is not None:
                body.append(line)
            # 조 밖의 줄(표지·목차·쪽 번호)은 버린다. 남기면 본문에 쪽 번호가 섞인다

        # 표는 줄 흐름과 별개로 페이지 단위로 뽑는다
        for tbl in page.find_tables().tables:
            rows = [[(c or "").strip() for c in r] for r in tbl.extract()]
            if not _valid_table(rows):
                out.counts["표: 무효(레이아웃 박스)"] = out.counts.get("표: 무효(레이아웃 박스)", 0) + 1
                continue
            sec = cur.section if cur is not None else f"p{pno + 1}"
            # **번호는 조 단위로 이어 센다.** 페이지마다 1부터 세면 **한 조가 두 쪽에 걸칠 때**
            # 두 표가 똑같이 `표1` 이 된다 (실측: `제도성 특별약관 제4조-4-표1` 두 번).
            # 조가 짧은 문서에서는 안 드러나던 자리다.
            seen_table[sec] += 1
            # `Table.title` 은 필수다. 조 제목이 없으면 섹션을 그대로 쓴다 —
            # 비워 두면 청커가 인용 문자열을 못 만든다
            out.elements.append(Table(
                id=f"{doc_id}#{sec}-표{seen_table[sec]}", title=(cur.title if cur else None) or sec,
                section=sec, header=rows[0], rows=rows[1:]))

    flush()
    flush_add()
    out.counts["약관"] = seen_terms
    out.counts["부칙"] = n_add
    return out
