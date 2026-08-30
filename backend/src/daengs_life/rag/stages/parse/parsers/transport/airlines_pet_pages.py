"""항공사 반려동물 안내 파서 — **한 소스에 사이트가 둘이고 모양이 완전히 다르다** (RAG-046).

크롤러 소스(`crawler/sources/transport/airlines_pet_pages.py`)가 두 항공사를 한 소스로 묶었다.
`source_url` 의 호스트로 갈린다(아래 `HOSTS`) — `srt_terms` 처럼 파일 하나가 사이트 하나였던
앞의 운송 파서들과 다른 점이다.

**갈리는 이유가 크다.** 둘은 "HTML 구조가 다르다" 수준이 아니라 **본문이 있는 자리가 다르다**:

  이스타       DOM 의 `<table>` 하나가 문서 전체다 (구분 × 국내선/국제선)
  에어프레미아  DOM 에는 표의 **머리만** 있고 값은 Next.js RSC 페이로드 안에 있다

그래서 에어프레미아 쪽은 `crawler.core.nextpayload` 로 i18n 사전을 읽어 IR 을 만든다.
**리더를 여기 복사하지 않는 이유**는 `jsobject` 와 같다 — 두 벌이 되면 한쪽만 고쳐지는 날
크롤러는 요금을 보는데 파서는 못 보는 상태가 된다 (`test_import_direction_packages` 의
`ALLOWED['rag']` 에 그 근거가 적혀 있다).

────────────────────────────────────────────────────────────────────────────
에어프레미아 — 평평한 사전을 다시 표로 세운다
────────────────────────────────────────────────────────────────────────────
페이로드는 키가 구조를 담고 있다:

    need_pet_fare_{32kg|33kg}_{nea|sea|useu}_{kr|local}

**이것을 평문으로 흘리지 않고 `Table` 로 되살린다.** 사용자의 질문이 "미주 노선 반려동물
요금"처럼 **구간 × 무게** 두 축으로 들어오는데, 평문이면 `KRW 580,000` 이 어느 칸의 값인지가
문장 안에서 사라진다. RAG-004 가 표를 따로 둔 이유가 그것이다.

⚠️ **구간 코드의 한글 이름은 페이로드에 없다.** 화면에서는 `#NEA`·`#SEA`·`#NAM` 버튼 라벨이
그 역할을 하는데 그것은 DOM 쪽이다. 코드→이름을 여기 표로 두고, **모르는 코드가 나오면
경고를 남긴다** — 조용히 코드를 그대로 쓰면 `useu` 라고 적힌 행이 코퍼스에 들어간다.

나머지 키는 접두사로 절을 나눈다 (`_guide_1_*` 사전 준비 / `_guide_2_*` 공항 도착 후 /
`_notes_*` 유의사항 / `_cancel_*` 취소·환불 / `_popup_*` 단두종·맹견·미국 규정).
**`_popup_*` 을 버리지 않는다** — 단두종·맹견 목록과 미국 입국 규정이 거기 있고, 그것이
"우리 개는 탈 수 있나"에 답하는 데 가장 직접적인 문단이다.

────────────────────────────────────────────────────────────────────────────
이스타 — 표 하나가 문서다
────────────────────────────────────────────────────────────────────────────
`htmltable.header_and_rows` 가 rowspan/colspan 을 펴 주므로 그대로 쓴다. 다만 머리가
`['구분','내용','내용']` 로 잡힌다 — 첫 데이터 행이 실제 머리(`국내선`/`국제선`)라서다.
그 행을 머리로 올린다.

⚠️ **다국어 제거는 `textutil.drop_class_suffix` 로 한다 — 크롤러와 같은 함수다.** 안 지우면
표 셀 하나에 한/일/중/대만/태가 쌓여 6,703자가 되고 렉시컬 축이 일본어를 친다.
그리고 **영어는 지우지 않는다** — 운송 요금이 영어 문장에만 있다 (`KRW 30,000 per segment`).
"""
from __future__ import annotations

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from daengs_life.crawler.core import nextpayload, textutil
from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.core.ir import Heading, Para, Table
from ..base import Parsed

NAME = "airlines_pet_html"
VERSION = 1

# 크롤러와 같은 값. 한 곳에서 읽어 오고 싶지만 `rag → crawler.sources` 는 막혀 있고(RAG-018),
# 그 방향을 뚫는 것보다 **두 줄이 어긋나면 테스트가 잡게** 하는 편이 싸다
# (`test_airlines_pet_pages.py::test_parser_and_crawler_share_the_language_rule`).
FOREIGN_SUFFIXES = ("JP", "CN", "TW", "TH", "VN")
PAYLOAD_PREFIX = "need_pet"

EASTAR_CONTAINER = "article.wrap"
# 표 제목. **`<caption>` 을 쓰지 않는다** — 캡션이 `반려동물을 동반하는 고객` 이라 문서 제목과
# 같아서, 청크 머리(`{문서 제목} {표 제목}`)가 "이스타항공 반려동물을 동반하는 고객 반려동물을
# 동반하는 고객" 으로 겹친다. 겹친 말은 임베딩에도 렉시컬에도 보탬이 없다.
EASTAR_TABLE_TITLE = "국내선 · 국제선 동반 기준"
AIRPREMIA_PANEL = "#need_pet_panel1"

# 페이로드의 구간 코드 → 화면에 뜨는 이름 (`#NEA`·`#SEA`·`#NAM` 버튼 라벨).
# 이름이 페이로드에 없어서 여기 둔다. 모르는 코드는 경고로 드러낸다.
REGIONS = {"nea": "동북아", "sea": "동남아", "useu": "미주"}
# 무게 구간 코드 → 이름. 값 키(`need_pet_fare_32kg`)가 라벨도 겸한다
WEIGHTS = ("32kg", "33kg")
ORIGINS = {"kr": "한국 출발", "local": "현지 출발"}

# 본문이 아닌 키. **버리되 왜 버리는지 적어 둔다** — 나중에 "왜 이 문장이 코퍼스에 없지"를
# 다시 추적하지 않기 위해서다.
#
# ⚠️ `need_pet_help*` 은 **장애고객 보조견 탭**이다. 크롤러가 DOM 쪽에서 `#need_pet_panel2` 를
# 뺐는데(같은 `h2` 가 겹쳐 청크 제목이 충돌한다) 페이로드에는 그 탭 문구도 같이 실려 있다.
# 여기서 안 빼면 **DOM 과 페이로드가 서로 다른 문서를 만든다.**
DROP_EXACT = {
    "need_pet": "탭 라벨",
    "need_pet_tagline": "히어로 문구 — UI 이지 안내가 아니다",
}
DROP_PREFIXES = ("need_pet_help",)      # 장애고객 보조견 탭 (위 ⚠️)

# 절 이름. 키 접두사가 이 순서로 문서를 이룬다
SECTIONS: list[tuple[str, str]] = [
    ("need_pet_species", "운송 가능한 동물"),
    ("need_pet_guide_1", "사전에 준비하기"),
    ("need_pet_guide_2", "공항에 도착한 후"),
    ("need_pet_cancel", "취소 · 환불 안내"),
    ("need_pet_notes", "유의사항"),
    ("need_pet_popup", "단두종 · 맹견 · 미국 입국 규정"),
]


# 어느 항공사인지는 **`source_url` 의 호스트로 가른다.**
#
# 크롤러의 `Target.meta["airline_key"]` 를 쓰고 싶지만 `.meta.json` 에는 그 필드가 없다 —
# `store.save` 가 documents 대응 필드만 골라 쓰기 때문이고(RAG-019 의 필드 목록), 그 목록을
# 이 카드에서 늘리면 소스 하나 때문에 모든 소스의 meta 규격이 바뀐다.
# 호스트는 **원본 파일 안에 이미 있는 사실**이라 새 규약을 만들지 않는다.
HOSTS = {"airpremia.com": "airpremia", "eastarjet.com": "eastar"}


def _airline_key(doc: RawDoc) -> str:
    host = urlparse(doc.meta.get("source_url", "")).hostname or ""
    for suffix, key in HOSTS.items():
        if host.endswith(suffix):
            return key
    return ""


def _title(doc: RawDoc) -> str:
    """문서 제목. **`with_org` 를 쓰지 않는다.**

    앞의 운송 파서들은 제목에 운영기관이 없어서 `with_org` 로 붙였다(RAG-034 ⑥). 여기는 반대다 —
    크롤러가 이미 `에어프레미아 …` · `이스타항공 …` 으로 제목을 만들고, 시드의 `org` 는
    **`국적 항공사 9개`** 라 그것을 앞에 붙이면 `국적 항공사 9개 에어프레미아 …` 가 된다.
    """
    return doc.meta.get("document_title", "") or "항공사 반려동물 안내"


def parse(raw: bytes, doc: RawDoc) -> Parsed:
    key = _airline_key(doc)
    if key == "airpremia":
        return _airpremia(raw, doc)
    if key == "eastar":
        return _eastar(raw, doc)
    raise RuntimeError(
        f"모르는 항공사다 — source_url={doc.meta.get('source_url')!r}. "
        f"크롤러가 어댑터를 늘렸으면 HOSTS 에도 넣어야 한다")


# ------------------------------------------------------------------ 이스타
def _eastar(raw: bytes, doc: RawDoc) -> Parsed:
    from daengs_life.rag.stages.parse.extract import htmltable

    soup = BeautifulSoup(raw, "lxml")
    box = soup.select_one(EASTAR_CONTAINER)
    if box is None:
        raise RuntimeError(f"본문 컨테이너({EASTAR_CONTAINER})가 없다 — 페이지 구조가 바뀌었다")
    for tag in box.select("script, style"):
        tag.decompose()
    dropped = textutil.drop_class_suffix(box, FOREIGN_SUFFIXES)

    title = _title(doc)
    doc_id = doc.doc_id
    elements: list = [Heading(id=f"{doc_id}#h2-0", level=2, text=title)]
    warnings: list[str] = []
    if not dropped:
        # 지울 것이 없다는 것은 언어 클래스 규약이 바뀌었다는 뜻이다. 그러면 일본어가 그대로 실린다
        warnings.append("다국어 블록을 하나도 못 지웠다 — 클래스 규약이 바뀌었는지 확인할 것")

    table = box.find("table")
    if table is None:
        raise RuntimeError("표가 없다 — 이스타는 수치가 전부 표에 있어 표가 곧 문서다")

    header, rows = htmltable.header_and_rows(table)
    header = _eastar_header(header, rows)

    elements.append(Table(id=f"{doc_id}#t-1", title=EASTAR_TABLE_TITLE,
                          header=header, rows=rows))

    return Parsed(elements=elements, document_title=title,
                  published_at=doc.meta.get("published_at"),
                  citation_url=doc.meta.get("source_url"),
                  counts={"표": 1, "표 행": len(rows), "지운 다국어 블록": dropped},
                  warnings=warnings)


def _eastar_header(header: list[str], rows: list[list[str]]) -> list[str]:
    """`['구분','내용','내용']` → `['구분','국내선','국제선']`.

    원본은 `내용` 한 칸을 colspan 으로 늘려 놓았고 `htmltable` 이 그것을 펴면 **두 칸이 같은
    이름**이 된다. 그러면 청크에 `내용: … 내용: …` 이 나란히 찍혀 국내선인지 국제선인지가
    사라진다. 두 칸의 실제 이름은 첫 데이터 행에 있다.

    ⚠️ **그 행을 머리로 옮기고 지우면 안 된다** — 거기에 `기내 반입만 가능, 위탁 운송 불가` 가
    붙어 있어 내용이기도 하다. 이름만 빌려 오고 행은 그대로 둔다.
    """
    if not rows or len(header) < 3 or len(set(header[1:])) != 1:
        return header
    labels = [c.split("(")[0].strip() for c in rows[0][1:]]
    return [header[0], *labels] if all(labels) else header


# ------------------------------------------------------------- 에어프레미아
def _airpremia(raw: bytes, doc: RawDoc) -> Parsed:
    payload = nextpayload.mapping(raw, PAYLOAD_PREFIX)
    if not payload:
        raise RuntimeError(
            "i18n 페이로드를 하나도 못 읽었다 — DOM 에는 요금이 없으므로 여기서 멈춘다")

    title = _title(doc)
    doc_id = doc.doc_id
    elements: list = [Heading(id=f"{doc_id}#h2-0", level=2, text=title)]
    warnings: list[str] = []
    used: set[str] = set()

    fare, unknown = _fare_table(payload, doc_id, used)
    if fare is not None:
        elements.append(fare)
    else:
        warnings.append("요금 키를 못 찾았다 — 페이로드 키 규약이 바뀌었다")
    if unknown:
        warnings.append(f"모르는 구간 코드: {', '.join(sorted(unknown))} — REGIONS 에 추가할 것")

    dropped = {k for k in payload
               if k in DROP_EXACT or k.startswith(DROP_PREFIXES)}
    used.update(dropped)

    n = 0
    for prefix, name in SECTIONS:
        keys = sorted((k for k in payload if k.startswith(prefix) and k not in used),
                      key=_screen_order)
        lines = [payload[k] for k in keys if payload[k]]
        if not lines:
            continue
        used.update(k for k in payload if k.startswith(prefix))
        n += 1
        elements.append(Heading(id=f"{doc_id}#h3-{n}", level=3, text=name))
        for i, line in enumerate(lines, 1):
            elements.append(Para(id=f"{doc_id}#p-{n}-{i}", text=line, level=4,
                                 section=name, cites=textutil.cites(line)))

    leftover = [k for k in payload if k not in used]
    if leftover:
        # **버리지 않는다** — `DROP_EXACT`/`DROP_PREFIXES` 에 없는 키는 새 절이 생긴 것일 수 있다.
        # 어디에도 안 붙는 값이 조용히 사라지면 다음 개정에서 그 절만 코퍼스에 없는 상태가 된다.
        n += 1
        elements.append(Heading(id=f"{doc_id}#h3-{n}", level=3, text="기타 안내"))
        for i, k in enumerate(leftover, 1):
            elements.append(Para(id=f"{doc_id}#p-{n}-{i}", text=payload[k], level=4,
                                 section="기타 안내", cites=textutil.cites(payload[k])))
        warnings.append(f"절에 안 붙는 키 {len(leftover)}개를 '기타 안내'로 모았다 — "
                        f"SECTIONS 를 늘릴지 확인할 것 (예: {leftover[0]})")

    return Parsed(elements=elements, document_title=title,
                  published_at=doc.meta.get("published_at"),
                  citation_url=doc.meta.get("source_url"),
                  counts={"페이로드 키": len(payload), "버린 키": len(dropped),
                          "문단": len(elements) - 1},
                  warnings=warnings)


# 이름만으로는 화면 순서를 못 맞추는 자리. 화면은 `기내 반입 → 위탁` 인데 알파벳으로는
# `cargo` 가 `onboard` 앞이다. 아는 것만 고정하고 나머지는 이름 순서에 맡긴다.
SUBKEY_RANK = {"onboard": 0, "cargo": 1}


def _screen_order(key: str) -> tuple:
    """페이로드 순서가 **화면 순서가 아니라서** 키 이름으로 다시 세운다.

    실물에서 `need_pet_guide_1_1_dscrpt1`(설명)이 `need_pet_guide_1_1`(제목)보다 먼저 실려
    있었다. 그대로 두면 청크가 "…받아주세요. / 운송 승인 받기" 처럼 **설명 뒤에 제목**이 오는
    순서로 읽힌다. 답변을 만드는 쪽이 그 청크를 그대로 읽으므로 순서가 곧 읽기 품질이다.

    `_1_10` 이 `_1_2` 보다 뒤에 오도록 숫자는 숫자로 비교한다. 같은 자리에서는 **짧은 키가
    먼저**라 제목이 설명 앞에 선다 (`_1_1` < `_1_1_dscrpt1`).
    """
    out = []
    for p in key.split("_"):
        if p.isdigit():
            out.append((0, p.zfill(4), 0))
        elif p in SUBKEY_RANK:
            out.append((1, "", SUBKEY_RANK[p]))
        else:
            out.append((2, p, 0))
    return tuple(out)


def _fare_table(payload: dict[str, str], doc_id: str,
                used: set[str]) -> tuple[Table | None, set[str]]:
    """`need_pet_fare_{무게}_{구간}_{출발지}` 를 **구간 × 무게** 표로 되세운다."""
    unknown: set[str] = set()
    rows: list[list[str]] = []
    for region_code, region in REGIONS.items():
        for origin_code, origin in ORIGINS.items():
            cells = [payload.get(f"need_pet_fare_{w}_{region_code}_{origin_code}", "")
                     for w in WEIGHTS]
            if not any(cells):
                continue
            rows.append([f"{region} · {origin}", *cells])
            for w in WEIGHTS:
                used.add(f"need_pet_fare_{w}_{region_code}_{origin_code}")

    # REGIONS 에 없는 구간이 페이로드에 있으면 그 행이 통째로 빠진다. 조용히 빠지면 안 된다
    for k in payload:
        if not k.startswith("need_pet_fare_") or k in used:
            continue
        parts = k.removeprefix("need_pet_fare_").split("_")
        if len(parts) == 3 and parts[0] in WEIGHTS and parts[1] not in REGIONS:
            unknown.add(parts[1])

    if not rows:
        return None, unknown

    header = ["구분", payload.get("need_pet_fare_32kg", "32 kg 이하"),
              payload.get("need_pet_fare_33kg", "33 kg ~ 45 kg")]
    used.update({"need_pet_fare_32kg", "need_pet_fare_33kg"})
    # 표 밑에 붙는 단서들도 표의 일부다 — 통화 적용 규칙·홍콩 편도 제한이 여기 있다
    notes = [v for k, v in payload.items()
             if k.startswith("need_pet_fare_dscrpt") or k.startswith("need_pet_fare_from")
             or k in ("need_pet_fare_euNus",)]
    used.update(k for k in payload
                if k.startswith("need_pet_fare_dscrpt") or k.startswith("need_pet_fare_from")
                or k == "need_pet_fare_euNus")
    used.add("need_pet_fare")

    title = payload.get("need_pet_fare", "요금 안내")
    if notes:
        title = f"{title} — {notes[0]}" if len(notes[0]) < 40 else title
    return Table(id=f"{doc_id}#t-1", title=title, header=header, rows=rows), unknown
