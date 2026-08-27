"""`.meta.json` 의 source_id → 파서 모듈 매핑.

**크롤러와 같은 규약이다** (RAG-009, RAG-018) — 폴더를 스캔하지 않고 경로를 계산한다.

  raw/law/law-drf-api-...xml + meta{"source_id":"law-drf-api"}
    → rag.stages.parse.parsers.law.law_drf_api          (`-` → `_`)

domain 은 meta 에 없고 `raw/{domain}/` 폴더 이름이 곧 domain 이다.

**인덱싱하지 않기로 결정한 소스는 '미구현' 과 구분한다.** 둘 다 파서가 없지만 뜻이 정반대다 —
전자는 결정이고 후자는 할 일이다. CLI 출력에서 섞이면 "아직 안 만든 파서" 를 영영 못 알아챈다.
"""
from __future__ import annotations

import importlib
from types import ModuleType

# 파서를 만들지 않기로 한 소스 + 그 근거. 값은 CLI 에 그대로 출력된다.
NOT_INDEXED: dict[str, str] = {
    "law-animal-protection": "웹 원문 — API(law-drf-api)와 같은 법령. 출처 링크 생성용 (RAG-004/RAG-011)",
    "law-livestock-epidemic": "웹 원문 — 〃",
}


def module_name(domain: str, source_id: str) -> str:
    return f"rag.stages.parse.parsers.{domain.replace('-', '_')}.{source_id.replace('-', '_')}"


def resolve(domain: str, source_id: str) -> ModuleType | None:
    """파서 모듈. 없으면 None (= 아직 구현 안 됨)."""
    name = module_name(domain, source_id)
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        # 도메인 폴더가 통째로 없으면 e.name 이 상위 패키지다. 그것도 '미구현' 이지만
        # bs4 같은 외부 의존성 누락은 숨기지 않는다 (crawler.core.registry 와 같은 판정).
        if e.name and name.startswith(e.name):
            return None
        raise
