"""3단계 청커 — `parsed/` → `chunks/` (RAG-021).

**타입 기반 단일 청커다** (RAG-021 ①). 파서는 소스마다 갈랐지만(RAG-018) 청커는 그러지 않는다 —
RAG-004 판정표가 전부 "요소 타입만 보면 되는" 규칙이고, 그 `article` 이 어느 사이트에서 왔는지 알
필요가 없다. IR 6종을 고정한 목적이 이것이다. **새 소스가 들어와도 이 파일에 분기가 생기면 안 된다.**
생긴다면 IR 이 그 소스를 못 담고 있다는 신호이고 고칠 곳은 파서다.

조립 규칙은 RAG-021 ③ 이다. 요약하면 **캡션 + 본문**이고, 캡션에 무엇을 넣을지는
"청크를 떼어놓고 봤을 때 사용자 질의에 들어갈 말이 텍스트 안에 있는가" 로 정했다.
법령 조문 375개 중 자기 법령명이 본문에 등장하는 것이 **0건**이라 문서 제목은 전 타입에 붙는다.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..core import io
from ..core.ir import Chunk, ChunkSet

NAME = "structure"
VERSION = 1

# ---------------------------------------------------------------- 임계값 (전부 ADR 소관)
MAX_CHARS = 7_500        # ② 하드 상한. 넘으면 조용히 자르지 않고 실패시킨다
SOFT_CHARS = 2_000       # RAG-004 기준③. 넘어도 막지 않고 경고만 — ④ 가 폴백을 두지 않기로 했다
TABLE_WHOLE = 1_000      # ③(나) 표 전체가 이 이하면 통짜, 넘으면 논리 행 단위

_ART = re.compile(r"(?m)^제(\d+(?:조의\d+|조))\(")           # 부칙 안의 조 경계
_OTHER_LAW = re.compile(r"^제\d+(?:조의\d+|조)\((?:다른|타)\s*법[률령]+의?\s*개정")
_SUPPL_OTHER = re.compile(r"^부칙\s*\(")                     # 타법개정 부칙 (제목에 괄호)
_MARKER = re.compile(r"^\s*([가-힣]|\d{1,2})\s*[.)]")        # 별표 논리 행의 머리 마커
_EASYLAW_SUFFIX = re.compile(r"\s*\((?:본문|100문 100답)\)\s*$")


@dataclass
class Chunked:
    chunks: list[Chunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)     # 무엇을 왜 뺐는지 — 조용한 소실 방지


# ---------------------------------------------------------------- 문서 수준 값
def _last_segment(title: str) -> str:
    """easylaw 문서 제목은 사이트 경로 70자다. 마지막 마디만 쓴다 (③(가)).

    앞 3단(`반려동물과 생활하기 > …`)은 본문에서 복원 불가하지만 **질의어도 아니다.**
    `(본문)`·`(100문 100답)` 접미사는 우리 수집 아티팩트라 지운다.
    """
    return _EASYLAW_SUFFIX.sub("", title.split(">")[-1].strip())


def _doc_fields(head: dict) -> dict:
    """청크 행마다 복제되는 문서 수준 값 (⑤A — 행 하나가 자기완결적이다)."""
    return {
        "doc_id": head["doc_id"],
        # RAG-025 ④ — `source`(기관명)는 documents.source 컬럼이 기다리는 값이고,
        # `source_id` 는 여기 없어서 청커가 헤더를 들고 다니고 있었다(⑤A 가 없애려던 모양)
        "source": head.get("source", ""),
        "source_id": head.get("source_id", ""),
        "document_title": head.get("document_title", ""),
        "source_url": head.get("source_url"),
        "citation_url": head.get("citation_url"),
        "category": head.get("category", ""),
        "subcategory": head.get("subcategory", ""),
        "source_type": head.get("source_type", ""),
        "raw_file": head.get("raw_file", ""),
        "format": head.get("format", ""),
        "trust_level": head.get("trust_level", ""),
        "published_at": head.get("published_at"),
        "license": head.get("license", ""),
    }


def _make(base: dict, *, chunk_id: str, content: str, section: str | None,
          citation: str, element: dict, part: str | None = None) -> Chunk:
    return Chunk(chunk_id=chunk_id, content=content, section=section, citation=citation,
                 element_id=element["id"], element_type=element["type"],
                 part=part, chars=len(content), **base)


# ---------------------------------------------------------------- article (③(다))
def _paragraph_text(para: dict) -> str:
    """항 두문 + 그 항의 모든 호. 파서가 `①` 과 `1.` 을 이미 텍스트에 넣어 뒀으므로 이어붙이기다."""
    parts = [para.get("text", "")]
    for item in para.get("items", []):
        parts.append(item["text"])
        parts.extend(sub["text"] for sub in item.get("subitems", []))
    return "\n".join(p for p in parts if p)


def _article(el: dict, base: dict, title: str) -> list[Chunk]:
    head = f"{title} {el['head']}".strip()
    paras = el.get("paragraphs", [])
    body = "\n".join(t for t in (_paragraph_text(p) for p in paras) if t)
    full = f"{head}\n{body}" if body else head
    section = el.get("section") or ""

    if len(full) < SOFT_CHARS or not paras:
        return [_make(base, chunk_id=el["id"], content=full, section=section,
                      citation=f"{title} {section}".strip(), element=el)]

    # 2,000자 이상 — 항 두문 + 그 항의 모든 호 = 1청크. **호 단위 분할은 금지**다 (RAG-004):
    # 금액이 항 두문에만 있고 호에는 없어서, 호로 쪼개면 과태료 금액이 사라진다.
    out: list[Chunk] = []
    for i, para in enumerate(paras, start=1):
        sym = para.get("sym") or ""
        no = para.get("no") or i
        out.append(_make(
            base,
            chunk_id=f"{el['id']}{sym or f'제{no}항'}",
            content=f"{head}\n{_paragraph_text(para)}",     # head 를 청크마다 반복한다
            section=f"{section}제{no}항",
            citation=f"{title} {section}제{no}항",
            element=el,
        ))
    return out


# ---------------------------------------------------------------- 부칙 (①)
def _supplementary(el: dict, base: dict, title: str, dropped: Counter) -> list[Chunk]:
    """부칙은 구조 기반으로 가린다 — 전부 인덱싱도, 전부 제외도 아니다 (①)."""
    ttl = el.get("title") or "부칙"
    if _SUPPL_OTHER.match(ttl):
        dropped["부칙: 타법개정(제목)"] += 1
        return []

    text = el.get("text", "")
    hits = list(_ART.finditer(text))
    if not hits:
        # 조가 없는 단문 시행일 부칙. 시행일은 문서 헤더 published_at 과 article.effective_date 에
        # 이미 있고, 같은 문장이 여러 법령에 반복돼 적재 시 content_hash 로 합쳐지던 정체가 이것이다
        dropped["부칙: 단문 시행일"] += 1
        return []

    out: list[Chunk] = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        seg = text[m.start():end].strip()
        if _OTHER_LAW.match(seg):
            # 제목만으로는 못 걸러지는 것 — 부칙 '안의' 타법개정 조 (④ 에서 발견)
            dropped["부칙: 타법개정(조)"] += 1
            continue
        out.append(_make(
            base,
            chunk_id=f"{el['id']}제{m.group(1)}",
            content=f"{title} {ttl}\n{seg}",
            section=f"부칙 제{m.group(1)}",
            # 한 법령에 부칙이 여럿이고 각자 제1조를 갖는다. section 은 짧게 두고
            # 사람이 읽는 citation 에 호수를 넣어 구분한다 — 두 필드를 나눈 이유가 이것이다 (⑤B)
            citation=f"{title} {ttl} 제{m.group(1)}",
            element=el,
            part="supplementary",
        ))
    return out


# ---------------------------------------------------------------- table (③(나))
def _table_caption(el: dict, title: str) -> str:
    bits = [title, el.get("section") or "", el.get("title") or "", el.get("unit") or ""]
    return " ".join(b for b in bits if b).strip()


def _row_keys(rows: list[list[str]]) -> list[str]:
    """행 키 — 마커 우선, 없거나 그 표 안에서 중복이면 순번.

    마커를 가진 행이 56%뿐이고 그중 35건이 중복이라 **마커는 유일 키가 아니다** (⑤B).
    """
    raw: list[str | None] = []
    for r in rows:
        m = _MARKER.match(r[0]) if r else None
        raw.append(m.group(1) if m else None)
    dup = {k for k, c in Counter(k for k in raw if k).items() if c > 1}
    return [f"r{i}" if k is None or k in dup else k for i, k in enumerate(raw)]


def _table(el: dict, base: dict, title: str, dropped: Counter) -> list[Chunk]:
    header = el.get("header") or []
    rows = [r for r in (el.get("rows") or []) if any(c.strip() for c in r)]
    if header and all(not h.strip() for h in header):
        # 헤더가 전무하면 표가 아니라 레이아웃이다 — 세어 보니 9건 전부 서식이었다 (④ 에서 발견).
        # RAG-004 가 서식 126건을 뺀 것과 같은 종류다.
        dropped["표: 헤더 전무(서식)"] += 1
        return []
    if not rows:
        dropped["표: 빈 행뿐"] += 1
        return []

    cap = _table_caption(el, title)
    section = el.get("section") or el.get("title") or ""
    size = sum(len("".join(r)) for r in rows) + len("".join(header))

    if size <= TABLE_WHOLE:
        # 통짜 — 행마다 헤더를 반복하면 같은 문자열이 수십 번 들어간다. 이 표의 행은 혼자 서지 않는다
        lines = [cap]
        if any(h.strip() for h in header):
            lines.append(" | ".join(header))
        lines += [" | ".join(r) for r in rows]
        return [_make(base, chunk_id=el["id"], content="\n".join(lines), section=section,
                      citation=f"{title} {section}".strip(), element=el)]

    # 논리 행 단위 — 헤더와 값을 짝지어야 `20` 이 20원인지 20만원인지 알 수 있다 (RAG-004).
    # 헤더줄+값줄(B안)이 아니라 `헤더: 값`(A안)인 이유는 길이가 아니라 **거리**다 —
    # 질문 1 이 이 행과 만나려면 `과태료` 가 `20` 옆에 있어야 한다.
    out: list[Chunk] = []
    for key, row in zip(_row_keys(rows), rows):
        pairs = [f"{h}: {c}" for h, c in zip(header, row) if h.strip()]
        if not pairs:                     # 헤더가 일부만 빈 표 — 붙일 이름이 없는 칸은 값만 남긴다
            pairs = [c for c in row if c.strip()]
        marker = "" if key.startswith("r") else key
        # 마커 표기는 원문 그대로다. 층위(호/목/괄호숫자)를 파서가 판정하지 않았으므로
        # 우리가 '라목'·'제2호' 로 추론하면 틀린 인용을 만든다
        loc = f"{section} {marker}.".strip() if marker else section
        out.append(_make(
            base,
            chunk_id=f"{el['id']}-{key}",
            content="\n".join([cap, *pairs]),
            section=loc,
            citation=f"{title} {loc}".strip(),
            element=el,
        ))
    return out


# ---------------------------------------------------------------- easylaw / qa (③(라)(마))
def _easylaw_caption(lastseg: str, h1: str | None, h2: str | None) -> str:
    """문서 마디 + h1 + h2.

    **h1 을 넣는다.** h2 41개 중 `이동장비에 넣는 등 안전조치를 취한 후 탑승하기` 가 4번 반복되고
    (시내버스·고속버스·전철·기차), `(h1, h2)` 쌍으로는 중복이 0 이라 넷을 구분하는 축이 h1 뿐이다.
    서술문 h1 이 형제 주제어를 흘리는 손해는 순위 흔들림이지만, 빼면 구분 자체가 불가능해진다.
    """
    return "\n".join(x for x in (lastseg, h1, h2) if x)


def _qa(el: dict, base: dict) -> list[Chunk]:
    """질문 + 답 + 관련 법령.

    **사이트 문구 캡션은 붙이지 않는다** — 문서 제목 마지막 마디가 `반려동물을 지켜주세요.` 처럼
    질의어가 아니고, 10건이 캡션 5종을 나눠 써서 서로 비슷해진다.

    **대신 `related_laws` 를 본문에 넣는다.** qa 10건 중 본문에 법령명이 있는 것이 2건뿐이라,
    조문에서 법령명 복원율이 0% 였던 것과 같은 병리다. RAG-004 판정표의 "관련법령은 메타로" 를
    정정한 것이고, 9단계 답변이 인용할 조항 번호가 텍스트 안에 있게 된다.
    """
    lines = [el["question"], el["answer"]]
    laws = [x for x in el.get("related_laws", []) if x]
    if laws:
        lines.append("관련 법령: " + " · ".join(laws))
    ttl = el.get("title") or ""
    return [_make(base, chunk_id=el["id"], content="\n".join(lines), section=ttl or None,
                  citation=ttl, element=el)]


# ---------------------------------------------------------------- 본체
def chunk_doc(rows: list[dict]) -> Chunked:
    """parsed jsonl 한 개(헤더 + 요소) → 청크 목록."""
    head = rows[0]
    base = _doc_fields(head)
    title = head.get("document_title", "")
    lastseg = _last_segment(title)
    is_easylaw = head.get("source_id") == "easylaw-pet"
    res = Chunked()

    h1: str | None = None
    h2: str | None = None
    group: list[str] = []
    group_el: dict | None = None

    def flush() -> None:
        nonlocal group, group_el
        if group_el and group:
            cap = _easylaw_caption(lastseg, h1, h2)
            res.chunks.append(_make(
                base, chunk_id=group_el["id"], content="\n".join([cap, *group]),
                section=h2, citation=f"{lastseg} > {h2}" if h2 else lastseg, element=group_el))
        group, group_el = [], None

    for el in rows[1:]:
        t = el["type"]
        if t == "article":
            res.chunks += _article(el, base, title)
        elif t == "table":
            res.chunks += _table(el, base, title, res.dropped)
        elif t == "qa":
            res.chunks += _qa(el, base)
        elif t == "heading":
            if not is_easylaw:
                continue      # 법령의 장·절은 경계로만 쓴다 (RAG-004). 조문이 이미 법령명을 갖는다
            flush()
            if el.get("level") == 1:
                h1, h2 = el["text"], None
            else:
                h2, group_el = el["text"], el
        elif t == "aside":
            if not is_easylaw:
                res.dropped["aside: 별표 유래"] += 1     # ① 이 제외한 도식·수식 박스
                continue
            # 소제목 그룹에 흡수시키지 않고 독립 청크로 만든다 (③(마) 정정) — 질문 7 의 정답이
            # `※ 반려동물과 정부 지정 자연공원 이용하기` 박스인데 소제목과 주제가 다르다
            cap = _easylaw_caption(lastseg, h1, h2)
            at = el.get("title") or ""
            body = "\n".join(el.get("lines", []))
            res.chunks.append(_make(
                base, chunk_id=el["id"],
                content="\n".join(x for x in (cap, at, body) if x),
                section=at or h2,
                citation=" > ".join(x for x in (lastseg, h2, at) if x), element=el))
        elif t == "para":
            if "부칙" in (el.get("section") or "") + (el.get("title") or ""):
                res.chunks += _supplementary(el, base, title, res.dropped)
            elif is_easylaw and group_el is not None:
                group.append(el["text"])
            else:
                res.dropped["para: 소제목 밖"] += 1
    flush()

    for c in res.chunks:
        if c.chars > MAX_CHARS:
            raise ValueError(f"{c.chunk_id}: {c.chars}자 — ② 하드 상한 {MAX_CHARS} 초과")
        if c.chars > SOFT_CHARS:
            # ④ 는 폴백을 두지 않기로 했다. 막지 않고 드러낸다 — RAG-006 EDA 재료이자
            # 새 소스에서 초과가 늘면 ④ 를 재개할 트리거다
            res.warnings.append(f"{c.chunk_id}: {c.chars}자 (RAG-004 {SOFT_CHARS}자 초과)")
    return res


def chunk_file(parsed_path: Path) -> tuple[ChunkSet, Chunked]:
    rows = list(io.read(parsed_path))
    res = chunk_doc(rows)
    header = ChunkSet(
        doc_id=rows[0]["doc_id"],
        parsed_sha256=io.sha256_file(parsed_path),
        chunker=NAME,
        chunker_version=VERSION,
        chunked_at=io.now_kst(),
        counts=dict(Counter(c.element_type for c in res.chunks)),
        warnings=res.warnings,
    )
    return header, res


def content_hash(text: str) -> str:
    """`documents.content_hash` 와 같은 계산. **청커는 이 값을 산출물에 쓰지 않는다** (⑤D) —
    중복 적재 방지 자연키는 7단계 적재기 몫이다. 여기서는 **같은 content 가 생겼는지 경고**하는 데만
    쓴다. 합쳐지는 것 자체는 옳지만(내용이 같다) 조용한 것이 문제다."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
