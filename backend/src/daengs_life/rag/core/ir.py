"""공통 중간 표현(IR) — 모든 파서가 지켜야 할 계약 (RAG-018).

파서는 소스마다 다르다. 다른 것이 정상이고, 그 차이를 `parsers/{domain}/{id}.py` 파일 하나에
가둔다. **폭발을 막는 실제 장치는 파서 개수 통제가 아니라 산출물 모양의 고정**이다 —
청커·임베더·적재기·검색은 아래 6종만 알면 되고 소스가 20개가 되어도 안 늘어난다.

  article  번호 붙은 조항        조문, 약관 제○조
  heading  제목(level)          easylaw 소제목, 장·절, 공고문 `□ 지원대상`
  para     문단                 해설 본문, 별표 일반기준, 부칙
  table    표(header + rows)    별표 괘선표, 공고문 금액표
  aside    강조 박스·도식        easylaw tplv4, 별표 수식 박스
  qa       질문/답 쌍           easylaw 100문100답, 기관 FAQ

**7번째 타입을 만드는 기준** — 청킹 규칙이 기존 6종 중 어느 것과도 다를 때만. 그 외에는
기존 타입 + 속성으로 표현한다 (약관 특약 = `article` + `kind`).

`extra="forbid"` 인 이유 — 새 파서가 계약에 없는 필드를 슬쩍 얹으면 청커는 그것을 못 보고,
증상은 6단계 점수 하락으로만 나타난다. 그 자리에서 실패시키는 편이 싸다.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 조문 내부 구조
# 항 두문에만 금액이 있고 호에는 없다 (RAG-004). 계층을 살려 두어야 청커가 "항 두문 + 그 항의
# 모든 호" 를 한 청크로 만들 수 있다. 평평한 텍스트로 뭉개면 그 조립이 불가능해진다.
class SubItem(_Base):
    """목 — 가. 나. 다."""
    no: str
    text: str


class Item(_Base):
    """호 — 1. 2. 3."""
    no: str
    text: str
    subitems: list[SubItem] = []


class Paragraph(_Base):
    """항 — ① ② ③. 번호 없는 항(제2조처럼 호만 있는 경우)도 있다."""
    no: int | None = None
    sym: str | None = None
    text: str = ""
    items: list[Item] = []


# ---------------------------------------------------------------- 요소 6종
class _Element(_Base):
    id: str                                   # doc_id#섹션 — 청크 id 의 근간 (RAG-019)
    citation_url: str | None = None           # 있으면 문서 헤더보다 우선 (조문 딥링크 여지)


class Article(_Element):
    type: Literal["article"] = "article"
    section: str                              # 제15조 / 제12조의3 — 인용 문자열
    title: str | None = None
    head: str                                 # 제15조(등록대상동물의 등록 등)
    paragraphs: list[Paragraph] = []
    chars: int = 0                            # 청커의 2,000자 판정 입력 (RAG-004)
    key: str | None = None                    # 원본 조문키 — 역추적용
    effective_date: str | None = None


class Heading(_Element):
    type: Literal["heading"] = "heading"
    level: int
    text: str
    section: str | None = None                # 제1장 / 제2절
    site_id: str | None = None                # 사이트 내부 id (페이지 수정 시 바뀔 수 있어 참고용)


class Para(_Element):
    type: Literal["para"] = "para"
    text: str
    level: int | None = None
    marker: str | None = None                 # 1. / 가. / ※ / √
    title: str | None = None                  # 부칙 표제 등
    section: str | None = None
    cites: list[str] = []
    site_id: str | None = None


class Table(_Element):
    type: Literal["table"] = "table"
    title: str
    section: str | None = None                # 별표 4
    unit: str | None = None                   # (단위: 만원) — 표 전체에 한 번만 나온다 (RAG-004)
    header: list[str] = []
    rows: list[list[str]] = []
    related: str | None = None                # (제35조 관련)
    files: dict[str, str] = {}                # hwp/pdf 링크


class Aside(_Element):
    type: Literal["aside"] = "aside"
    lines: list[str] = []
    title: str | None = None
    section: str | None = None
    site_id: str | None = None


class QA(_Element):
    type: Literal["qa"] = "qa"
    title: str
    question: str
    answer: str
    related_laws: list[str] = []
    related_topic: str | None = None


AnyElement = Annotated[
    Union[Article, Heading, Para, Table, Aside, QA], Field(discriminator="type")
]
ELEMENT_ADAPTER: TypeAdapter[AnyElement] = TypeAdapter(AnyElement)
ELEMENT_TYPES = ("article", "heading", "para", "table", "aside", "qa")


# ---------------------------------------------------------------- 문서 헤더 (jsonl 1행)
class Document(_Base):
    """parsed jsonl 의 첫 줄. `.meta.json` 의 documents 대응 필드를 그대로 옮기고 파서 정보를 더한다."""
    type: Literal["document"] = "document"
    doc_id: str
    source_id: str
    domain: str

    # 콘텐츠 생성 주체(기관명). `.meta.json` 에 있는데 RAG-019 가 필드 목록에서 빠뜨렸다 —
    # `documents.source` 컬럼이 이 값을 기다린다 (RAG-025 ④). 옛 parsed 를 읽을 수 있게 기본값을 둔다
    source: str = ""
    category: str
    subcategory: str
    trust_level: str
    source_type: str
    format: str
    license: str = ""
    document_title: str
    published_at: str | None = None

    # source_url = 원본을 받은 곳(API 주소는 키가 마스킹돼 사람이 못 연다)
    # citation_url = 답변에 실을 링크. 둘을 나누지 않으면 그 if 가 청커·적재기·앱 세 층에 복제된다 (RAG-019)
    source_url: str | None = None
    citation_url: str | None = None

    raw_file: str
    raw_sha256: str                           # meta 의 sha256 — 재파싱 스킵 판단 (RAG-019)
    indexable: bool = True

    parser: str
    parser_version: int
    parsed_at: str
    counts: dict[str, int] = {}
    warnings: list[str] = []
    # 파서만 아는 부가정보(공포일자·소관부처 등). documents 컬럼이 아니라 역추적·EDA(RAG-006)용이라
    # 스키마를 강제하지 않는다.
    extra: dict[str, str | None] = {}


# ---------------------------------------------------------------- 청크 (3단계 산출물, RAG-021 ⑤)
class Chunk(_Base):
    """`chunks/*.jsonl` 의 한 줄. **행 하나가 자기완결적이다** (RAG-021 ⑤A).

    RAG-019 는 parsed 에서 문서 수준 값을 헤더에 몰았고 근거는 "`citation_url` 분기가 세 층에
    복제된다" 였다. 청크 단계에서는 그 분기가 이미 파서에서 끝나 값만 물려받으므로 같은 근거가
    성립하지 않는다. 반대로 하류 세 층(임베더·평가·적재기)이 전부 **파일 경계 없이** 읽기 때문에
    헤더에 두면 그 세 층이 헤더를 들고 다녀야 한다.
    """
    type: Literal["chunk"] = "chunk"

    # ----- documents 컬럼에 1:1 로 꽂히는 것 (RAG-008)
    chunk_id: str                             # → metadata.chunk_id. 골든셋(5단계)이 정답을 가리키는 주소
    content: str                              # → content. 임베딩 입력이 되는 텍스트 그대로
    section: str | None = None                # → section. 제15조제2항 / 별표 4 (RAG-019 §3)
    document_title: str                       # → document_title
    source: str = ""                          # → source. 콘텐츠 생성 주체(기관명) — RAG-025 ④
    source_url: str | None = None             # → source_url (원본을 받은 곳)
    category: str = ""
    subcategory: str = ""
    source_type: str = ""

    # ----- documents.metadata 로 갈 것
    raw_file: str = ""
    format: str = ""
    trust_level: str = ""
    published_at: str | None = None
    license: str = ""
    citation_url: str | None = None           # 답변에 실을 링크. source_url 과 다르다 (RAG-019 §4)

    # ----- 우리만 쓰는 것
    citation: str = ""                        # 사람이 읽을 인용 문자열. chunk_id 와 겸하지 않는다 (RAG-021 ⑤B)
    doc_id: str = ""
    source_id: str = ""                       # 크롤러 소스 계약 id (RAG-009). → metadata.source_id
    element_id: str = ""                      # 이 청크가 나온 IR 요소 — 역추적용
    element_type: str = ""
    part: str | None = None                   # "supplementary" = 부칙 (RAG-021 ①). 6단계에서 필터로 끌 자리
    chars: int = 0


class ChunkSet(_Base):
    """`chunks/*.jsonl` 의 첫 줄. **파일 수준 사실만** 담는다 — 문서 수준 값은 청크 행에 있다.

    `parsed_sha256` 은 재청킹 스킵 판단용이다. 크롤러의 `sha256` → parsed 의 `raw_sha256` →
    여기의 `parsed_sha256` 으로 같은 축이 한 단계 더 연장된다 (RAG-001 원칙 2).
    """
    type: Literal["chunkset"] = "chunkset"
    doc_id: str
    parsed_sha256: str
    chunker: str
    chunker_version: int
    chunked_at: str
    counts: dict[str, int] = {}
    warnings: list[str] = []
