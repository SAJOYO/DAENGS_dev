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

    def __init__(self, seed: dict[str, Any]) -> None:
        self.seed = seed                     # yaml 항목 원본 (org, title, url, notes …)

    @property
    def org(self) -> str:
        return self.seed.get("org", "")

    @abstractmethod
    def discover(self, fetcher: Fetcher) -> list[Target]: ...

    @abstractmethod
    def extract(self, res: FetchResult, target: Target) -> Extracted: ...
