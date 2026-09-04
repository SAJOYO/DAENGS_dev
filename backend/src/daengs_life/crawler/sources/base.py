"""소스 모듈이 구현해야 하는 최소 계약.

소스 하나 = 모듈 하나. 책임은 딱 둘:
  discover()  : 이 소스에서 받을 대상(URL + 파일명 slug) 목록
  extract()   : 받은 응답에서 제목/본문/인용 조항 뽑기 (dry-run 미리보기 + 이후 파싱 단계에서 재사용)

받기·저장·로그·변경 감지는 전부 core/ 가 한다. 소스 모듈은 site-specific 지식만 가진다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..core.fetch import FetchResult, Fetcher


@dataclass
class Target:
    """받을 문서 하나."""
    url: str
    slug: str                                # raw/ 파일명 (확장자 제외). data/README.md 규칙 2
    ext: str                                 # html | pdf | xml | json …
    meta: dict[str, Any] = field(default_factory=dict)   # 소스가 미리 아는 메타(제목 등). extract 결과가 덮어씀


@dataclass
class Extracted:
    """extract() 결과. text 는 변경 감지 지문(sha256) 계산에 쓰인다."""
    title: str
    text: str
    published_at: str | None = None          # YYYY-MM-DD
    cites: list[str] = field(default_factory=list)   # 본문이 인용한 조항 ("동물보호법 제16조제2항")
    extra: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    """seed_sources.yaml 의 한 항목에 대응. 클래스 속성은 .meta.json 으로 그대로 흘러간다."""

    id: str                                  # yaml id 와 동일
    domain: str                              # raw/ 하위 폴더 (law, registration …)
    category: str                            # documents.category (CHECK)
    subcategory: str                         # kebab-case
    source_type: str                         # document | web | api | manual (CHECK)
    format: str                              # pdf | hwp | hwpx | html | xml | json
    trust_level: str                         # law | official | guideline
    license: str = ""
    # 변경 감지 지문을 무엇으로 낼지. 기본은 원본 바이트고 html 은 늘 텍스트다 (store.py 머리).
    # `"text"` 로 두면 다른 format 도 `extract().text` 로 지문을 낸다 — 응답에 총건수·타임스탬프처럼
    # **문서와 무관하게 매번 바뀌는 값**이 박혀 오는 API 가 쓴다 (seoul-notice-api, RAG-053).
    fingerprint: str = "bytes"               # bytes | text
    # 개정 판정의 근거 (core/revision.py, RAG-054). 없으면 이 소스의 변경은 "바뀜"으로만 남는다.
    # fingerprint 와 층이 다르다 — 저것은 "무엇으로 sha 를 내나"(저장), 이것은 "sha 차이가 개정인가"(판정).
    #   "published_at"  같은 slug 의 시행일자가 달라지면 개정 — 법령
    #   "slug"          같은 제목의 새 slug 가 나타나면 옛 판이 대체된 것 — 약관 (판이 파일명에 있다)
    revision_key: str | None = None

    def __init__(self, seed: dict[str, Any]) -> None:
        self.seed = seed                     # yaml 항목 원본 (org, title, url, notes …)

    @property
    def org(self) -> str:
        return self.seed.get("org", "")

    @abstractmethod
    def discover(self, fetcher: Fetcher) -> list[Target]: ...

    @abstractmethod
    def extract(self, res: FetchResult, target: Target) -> Extracted: ...
