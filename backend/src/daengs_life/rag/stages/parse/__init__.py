"""2단계 파싱 — `raw/` → `processed/parsed/` (RAG-018, RAG-019).

**이 단계만 소스별로 코드가 갈린다.** 그래서 안에 3층이 있다 (RAG-018):

  ① `extract/`   포맷 층 — PDF·HWPX·괘선표를 어떻게 여나. 사이트를 모른다
  ② `parsers/`   사이트 층 — 그 사이트의 태그·클래스·표 관례
  ③ `rag.core.ir` 계약 — 산출물 모양. 청커·임베더·적재기는 이것만 안다

이 3층은 **파싱을 어떻게 하나의 구조**이지 패키지 전체의 구조가 아니다. RAG-023 이 그것을
parse 단계 안으로 넣은 이유가 이것이다 — 밖에 있으면 `chunk.py` · `embed.py` 와 같은 평면에
놓여 "왜 얘는 폴더고 얜 파일인가" 로 읽힌다.

`status()` 와 `parse_doc()` 은 원래 `__main__.cmd_parse` 안에 있었다. 다른 단계는 전부 자기
모듈에 로직이 있는데 parse 만 CLI 에 있어서 단계마다 모양이 달랐다 (RAG-023 배경 ②).
CLI 는 이제 여기를 부르고 출력만 한다.
"""
from __future__ import annotations

import collections
from typing import Any

from daengs_life.rag.core import io
from daengs_life.rag.core.ir import AnyElement, Document

from . import registry

__all__ = ["registry", "status", "parse_doc"]


def status(doc: io.RawDoc) -> tuple[str, str]:
    """(라벨, 사유). **'인덱싱 안 함'(결정)과 '파서 없음'(할 일)을 섞지 않는다** — RAG-018.

    둘 다 파서가 없지만 뜻이 정반대다. CLI 출력에서 섞이면 아직 안 만든 파서를 영영 못 알아챈다.
    """
    if doc.source_id in registry.NOT_INDEXED:
        return "NOT-INDEXED", registry.NOT_INDEXED[doc.source_id]
    if registry.resolve(doc.domain, doc.source_id) is None:
        return "TODO", f"파서 없음: {registry.module_name(doc.domain, doc.source_id)}.py"
    return "OK", ""


def parser_version(doc: io.RawDoc) -> int | None:
    """이 원본을 맡은 파서 모듈의 `VERSION`. 파서가 없으면 `None`.

    `io.is_current()` 에 넘겨 **파서를 고쳤는데 옛 결과가 남는 것**을 막는다 (RAG-066 ②).
    여기 두는 이유는 registry 를 아는 층이 여기라서다 — `io` 가 파서를 import 하면 순환이다.
    """
    mod = registry.resolve(doc.domain, doc.source_id)
    return getattr(mod, "VERSION", None) if mod else None


def parse_doc(doc: io.RawDoc) -> tuple[Document, list[AnyElement], dict[str, int]]:
    """원본 1건 → (문서 헤더, 요소들, 타입별 개수). 쓰지는 않는다 — 그건 호출자 몫이다.

    쓰기를 분리해 둔 이유는 `--dry-run` 이 여기까지만 돌면 되기 때문이고, 6단계 이후에
    파싱만 다시 돌려 보는 자리가 생기기 때문이다.
    """
    mod = registry.resolve(doc.domain, doc.source_id)
    parsed = mod.parse(doc.path.read_bytes(), doc)

    counts = collections.Counter(e.type for e in parsed.elements)
    meta: dict[str, Any] = doc.meta
    header = Document(
        doc_id=doc.doc_id,
        source_id=doc.source_id,
        domain=doc.domain,
        # `.meta.json` 이 유일하게 아는 값이다. 여기서 안 실으면 하류 어디에도 없다 (RAG-025 ④)
        source=meta.get("source") or "",
        category=meta["category"],
        subcategory=meta.get("subcategory") or "",
        trust_level=meta.get("trust_level") or "",
        source_type=meta.get("source_type") or "",
        format=meta.get("format") or "",
        license=meta.get("license") or "",
        document_title=parsed.document_title or meta.get("document_title") or "",
        published_at=parsed.published_at or meta.get("published_at"),
        source_url=meta.get("source_url"),
        # 파서가 풀어 준 값이 없으면 원본을 받은 곳을 그대로 쓴다 (easylaw 처럼 둘이 같은 경우)
        citation_url=parsed.citation_url or meta.get("source_url"),
        raw_file=meta["raw_file"],
        raw_sha256=meta["sha256"],
        parser=mod.NAME,
        parser_version=mod.VERSION,
        parsed_at=io.now_kst(),
        counts={**counts, **parsed.counts},
        warnings=parsed.warnings,
        extra=parsed.extra,
    )
    return header, parsed.elements, dict(counts)
