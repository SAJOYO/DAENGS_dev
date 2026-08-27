"""파서 모듈이 구현해야 하는 최소 계약 (RAG-018 ②사이트 층).

모듈 하나 = 소스 하나. 노출하는 것은 셋뿐이다.

  NAME     : parsed 헤더에 남길 파서 이름
  VERSION  : 파서를 고쳐 재파싱이 필요해지면 올린다 (RAG-006 이 "구버전으로 처리된 문서" 를 고를 근거)
  parse()  : 원본 바이트 + meta → Parsed

받기·저장·스킵 판단은 `rag/core/io.py` 와 CLI 가 한다. 파서는 **그 사이트의 구조만** 안다.
포맷 처리(괘선표 해석, HTML 블록 추출)는 `rag/stages/parse/extract/` 로 올린다 — 사이트가 달라도 반복되기 때문.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from daengs_life.rag.core.ir import AnyElement


@dataclass
class Parsed:
    """파서 결과. 문서 헤더 필드 중 **파서만 알 수 있는 것**을 함께 돌려준다.

    나머지 헤더 필드(category·trust_level·license …)는 `.meta.json` 에서 그대로 옮기므로
    파서가 다시 말할 필요가 없다.
    """
    elements: list[AnyElement]
    document_title: str | None = None
    published_at: str | None = None
    citation_url: str | None = None          # 답변에 실을 링크 (RAG-019). None 이면 source_url 을 쓴다
    counts: dict[str, int] = field(default_factory=dict)   # 파서만 아는 수치 (제외한 서식 수 등)
    warnings: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
