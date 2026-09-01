"""괘선 아트로 그려진 고정폭 표 파서 (RAG-020).

법령 별표는 HWP 를 고정폭 텍스트로 뽑은 것이라 표가 `┌─┬─┐ │…│` 로 그려져 있다.
한 논리 행이 물리적으로 2~11줄에 걸치고 컬럼이 줄 단위로 뒤섞이므로, 줄을 그냥 이어붙이면
`아. 소유자등이 법 제16조제2항제1호에  법 제101조  20  30  50 따른 안전조치를…` 처럼
문장과 금액이 엉키고 `제101조제4항제4호` 라는 인용 문자열을 복원할 수 없다 (RAG-020 의 B안).

**사이트 지식이 아니라 포맷 지식이라 `extract/` 에 둔다.** 지자체 공고문 금액표·펫보험 약관
보장표가 같은 형태라 그대로 재사용한다.

핵심 두 가지
  1. `│` 위치는 **표시 폭**(한글 2칸)으로 재야 한다. 문자 인덱스로는 헤더와 본문이 정렬되지 않는다
  2. 논리 행 경계는 **빈 셀 행**(`│  │  │`) 또는 `├` 구분선이다. `가./나./다.` 마커보다 확실하다 —
     원본 표의 행 경계가 그대로 남은 신호이기 때문
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

# 같은 표 안에서도 light(`─│┌┬`)와 heavy(`━┃┏┳`)가 섞여 나온다 — 원본 HWP 가 선 굵기를
# 달리 쓴 것이 그대로 넘어온 것이다. 글자를 열거하지 않고 **유니코드 블록으로 판정**한다.
BOX_BLOCK = range(0x2500, 0x2580)          # Box Drawing
VBARS = "│┃"                                # 셀 구분자 — 빈 셀 행에도 나오므로 괘선과 구분해야 한다
LEFT_JUNCTIONS = "├┝┠┣"                     # 행 구분선의 왼쪽 끝 (VERTICAL AND RIGHT 계열)


def is_box(ch: str) -> bool:
    return ord(ch) in BOX_BLOCK


def is_rule_line(line: str) -> bool:
    """셀 내용이 없는 순수 괘선 줄. `│  │  │` 같은 빈 셀 행은 여기 해당하지 않는다."""
    return any(is_box(c) and c not in VBARS for c in line)


def strip_box(line: str) -> str:
    return "".join(" " if is_box(c) else c for c in line).strip()

# 새 문단이 시작되는 줄. 나머지는 앞 문단의 hard-wrap 이라 공백 없이 이어붙인다
# (원본이 셀 폭에 맞춰 임의 위치에서 끊으므로 공백을 넣으면 `위반하 여` 가 된다 — RAG-020)
_MARKER = re.compile(r"^\s*(?:[■※☞□○●▪]|\d+\.|\d+\)|[가-힣]\.|[가-힣]\)|\(\d+\)|\(단위|[①-⑳])")


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


@dataclass
class TextBlock:
    paragraphs: list[str] = field(default_factory=list)
    degraded: bool = False        # 표처럼 보였으나 격자를 못 세운 구간 (꼬리표 같은 서식 그림)


@dataclass
class TableBlock:
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    @property
    def n_cols(self) -> int:
        return max(len(self.header), max((len(r) for r in self.rows), default=0))


Block = TextBlock | TableBlock


# ------------------------------------------------------------------ 저수준
def _bar_offsets(line: str) -> tuple[int, ...]:
    off, acc = [], 0
    for c in line:
        if c in VBARS:
            off.append(acc)
        acc += 2 if unicodedata.east_asian_width(c) in "WF" else 1
    return tuple(off)


def _cells(line: str, right: int) -> list[tuple[int, int, str]]:
    """(시작 offset, 끝 offset, 텍스트). 병합 셀은 넓은 구간 하나로 나온다.

    **첫 `│` 앞과 마지막 `│` 뒤도 셀로 낸다.** 바깥 테두리 없이 가운데 `│` 하나로만 나눈 표가
    있기 때문이다(시행규칙 별표 12 영업자의 준수사항). 테두리가 있는 표에서는 그 두 조각이
    빈 문자열이라 그냥 걸러진다.
    """
    out: list[tuple[int, int, str]] = []
    acc, buf, start = 0, [], 0
    for c in line:
        if c in VBARS:
            out.append((start, acc, "".join(buf).strip()))
            buf, start = [], acc
        else:
            buf.append(c)
        acc += 2 if unicodedata.east_asian_width(c) in "WF" else 1
    # 줄마다 길이가 달라 마지막 셀의 끝을 그 줄 길이로 두면 오른쪽 끝 컬럼에 안 걸린다
    out.append((start, max(acc, right), "".join(buf).strip()))
    return out


def _join_wrapped(lines: list[str]) -> list[str]:
    """마커로 시작하는 줄에서 문단을 끊고, 나머지는 공백 없이 잇는다."""
    paras: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if not paras or _MARKER.match(raw):
            paras.append(s)
        else:
            paras[-1] += s
    return paras


def _has_outer_text(tlines: list[str]) -> tuple[bool, bool]:
    """첫 `│` 앞 / 마지막 `│` 뒤에 글자가 있는가 = 바깥 테두리가 없는 표인가."""
    lead = tail = False
    for line in tlines:
        if is_rule_line(line):
            continue
        bars = [i for i, c in enumerate(line) if c in VBARS]
        if not bars:
            continue
        lead = lead or bool(line[: bars[0]].strip())
        tail = tail or bool(line[bars[-1] + 1:].strip())
    return lead, tail


# ------------------------------------------------------------------ 표
def parse_table(tlines: list[str]) -> TableBlock:
    """괘선 구간 → 헤더 + 논리 행. 격자를 못 세우면 빈 TableBlock 을 돌려준다."""
    grid = Counter(_bar_offsets(l) for l in tlines
                   if any(c in VBARS for c in l) and strip_box(l))
    if not grid:
        return TableBlock()
    # 데이터 행의 최빈 `│` 배치가 컬럼 경계다. 헤더는 병합 셀이라 배치가 다르다
    cols = max(grid.items(), key=lambda kv: (kv[1], len(kv[0])))[0]

    # 바깥 테두리가 없는 표를 위해 양 끝을 보충한다. 판별 기준은 "폭이 남았는가" 가 아니라
    # **첫 `│` 앞이나 마지막 `│` 뒤에 실제 글자가 있는가** 다 — 헤더 줄이 본문보다 몇 칸 넓게
    # 그려진 표(별표 4)가 있어 폭으로 재면 빈 컬럼이 하나 더 생긴다.
    right = max((width(l) for l in tlines), default=0)
    lead, tail = _has_outer_text(tlines)
    edges = list(cols)
    if lead and edges and edges[0] > 0:
        edges.insert(0, 0)
    if tail and edges and edges[-1] < right:
        edges.append(right)
    bounds = list(zip(edges, edges[1:]))
    if not bounds:
        return TableBlock()

    def spans(a: int, b: int) -> list[int]:
        return [i for i, (s, e) in enumerate(bounds) if a <= s and b >= e]

    header: list[list[str]] = [[] for _ in bounds]
    cur: list[list[str]] = [[] for _ in bounds]
    rows: list[list[str]] = []
    # `├──┼──┤` 가 하나도 없으면 헤더/본문 구분이 없는 표다 (도식·수식 박스). 전부 본문으로 본다
    has_sep = any(l.lstrip()[:1] in LEFT_JUNCTIONS for l in tlines)
    seen_sep = not has_sep

    def flush() -> None:
        nonlocal cur
        if any(any(p) for p in cur):
            rows.append(["".join(p) for p in cur])
            cur = [[] for _ in bounds]

    for line in tlines:
        if is_rule_line(line):                            # 순수 괘선 줄
            if seen_sep:
                # 괘선 줄은 모두 행 구분선으로 본다. 위·아래 테두리에서는 비어 있어 무해하고,
                # 테두리 없는 표는 `────┼────` 만으로 행을 나누므로 이게 유일한 신호다
                flush()
            elif line.lstrip()[:1] in LEFT_JUNCTIONS:
                seen_sep = True                           # 여기까지가 헤더
            continue
        cs = _cells(line, right)
        if not any(t for *_, t in cs):                    # 빈 셀 행 = 논리 행 구분자
            flush()
            continue
        for a, b, text in cs:
            if not text:
                continue
            for i in spans(a, b):
                (cur if seen_sep else header)[i].append(text)
    flush()
    # 헤더의 물리적 줄은 의미 단위(`1차` / `위반`)라 본문과 규칙이 다르다 — 공백으로 잇는다 (RAG-020)
    return TableBlock(header=[" ".join(h) for h in header], rows=rows)


def parse(text: str) -> list[Block]:
    """별표 본문 → 텍스트 구간 / 표 구간의 나열. 원본 순서를 보존한다."""
    lines = [l.rstrip() for l in text.split("\n")]
    lines = [l for l in lines if l.strip()]               # 별표 4 는 999줄 중 327줄만 내용이다

    segments: list[tuple[bool, list[str]]] = []
    cur: list[str] = []
    in_table = False
    for line in lines:
        is_table = any(is_box(c) for c in line)
        if is_table != in_table:
            if cur:
                segments.append((in_table, cur))
            cur, in_table = [], is_table
        cur.append(line)
    if cur:
        segments.append((in_table, cur))

    blocks: list[Block] = []
    for is_table, seg in segments:
        if not is_table:
            blocks.append(TextBlock(paragraphs=_join_wrapped(seg)))
            continue
        tb = parse_table(seg)
        if tb.rows:
            blocks.append(tb)
        else:
            # 격자를 못 세운 구간 — 괘선으로 그린 서식(꼬리표·봉함표)이다. 판단은 호출자에게 넘긴다
            blocks.append(TextBlock(paragraphs=_join_wrapped([strip_box(l) for l in seg]),
                                    degraded=True))
    return blocks
