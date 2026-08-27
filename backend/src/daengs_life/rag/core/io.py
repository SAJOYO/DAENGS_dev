"""parsed/ 입출력 — 원본 1건 = jsonl 1개 (RAG-019).

**재파싱 스킵을 크롤러와 같은 축으로 맞춘다.** `.meta.json` 의 `sha256` 은 원본 1건 단위이고,
parsed 헤더에 그 값을 `raw_sha256` 으로 적어 두면 다음 실행에서 비교만으로 건너뛸 수 있다.
별도 상태 파일이 필요 없다 — RAG-001 원칙 2 를 파싱 단계까지 연장한 것이다.

`.meta.json` 이 없는 원본은 **읽지 않는다** (data/README.md 규칙 3: meta 없는 파일은 인덱싱 금지).
출처를 못 대는 문서는 "출처 링크 + 조항 번호" 가 KPI 인 이 시스템에서 가치가 없다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from . import config


@dataclass
class RawDoc:
    """원본 하나 + 그 meta. meta 가 가리키는 파일이 실제로 있는 것만 만든다."""
    meta: dict[str, Any]
    path: Path
    meta_path: Path

    @property
    def doc_id(self) -> str:
        return self.path.stem                 # law-drf-api-animal-protection-act__20260820

    @property
    def source_id(self) -> str:
        return self.meta.get("source_id", "")

    @property
    def domain(self) -> str:
        # meta 에 domain 필드는 없다 — raw/{domain}/ 폴더가 곧 domain 이다 (RAG-009)
        return self.path.parent.name


def raw_docs(source_id: str | None = None) -> list[RawDoc]:
    config.require_data_dir()
    out: list[RawDoc] = []
    for meta_path in sorted(config.RAW_DIR.glob("*/*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if source_id and meta.get("source_id") != source_id:
            continue
        rel = meta.get("raw_file")
        if not rel:
            continue
        path = config.RAW_DIR / rel
        if not path.is_file():                # RAG-010 과 같은 상황. 여기서는 크롤러를 다시 돌리라고만 한다
            continue
        out.append(RawDoc(meta=meta, path=path, meta_path=meta_path))
    return out


def parsed_path(doc_id: str) -> Path:
    return config.PARSED_DIR / f"{doc_id}.jsonl"


def read_header(path: Path) -> dict[str, Any] | None:
    """jsonl 첫 줄만 읽는다. 파일이 깨졌으면 없는 것으로 본다 — 다시 파싱하면 되는 산출물이다."""
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.loads(f.readline())
    except (json.JSONDecodeError, OSError):
        return None


def is_current(doc: RawDoc) -> bool:
    """이미 같은 원본으로 파싱된 결과가 있는가."""
    head = read_header(parsed_path(doc.doc_id))
    return bool(head) and head.get("raw_sha256") == doc.meta.get("sha256")


def now_kst() -> str:
    return datetime.now(config.KST).isoformat(timespec="seconds")


def write(header: BaseModel, elements: list[BaseModel]) -> Path:
    """1행 문서 헤더 + 2행부터 요소. 문서 순서를 보존한다."""
    path = parsed_path(header.doc_id)         # type: ignore[attr-defined]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for model in [header, *elements]:
            f.write(json.dumps(model.model_dump(exclude_none=True), ensure_ascii=False) + "\n")
    return path


def read(path: Path) -> Iterator[dict[str, Any]]:
    """3단계 청커가 쓴다."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


# ---------------------------------------------------------------- 3단계: chunks/ (RAG-021 ⑤)
def parsed_files() -> list[Path]:
    config.require_data_dir()
    return sorted(config.PARSED_DIR.glob("*.jsonl"))


def chunk_path(doc_id: str) -> Path:
    return config.CHUNK_DIR / f"{doc_id}.jsonl"


def sha256_file(path: Path) -> str:
    """parsed jsonl 한 개의 해시. 재청킹 스킵의 비교 대상이다."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def is_chunk_current(parsed: Path) -> bool:
    """이미 같은 parsed 로 청킹된 결과가 있는가.

    parsed 쪽 `is_current()` 와 같은 모양이다 — 상태 파일을 따로 두지 않고 상류 산출물의 해시를
    하류 헤더에 적어 비교한다 (RAG-001 원칙 2 를 한 단계 더 연장).
    """
    head = read_header(chunk_path(parsed.stem))
    return bool(head) and head.get("parsed_sha256") == sha256_file(parsed)


def write_chunks(header: BaseModel, chunks: list[BaseModel]) -> Path:
    """1행 헤더 + 2행부터 청크. parsed 와 같은 파일 모양이라 `read()` 를 그대로 쓴다."""
    path = chunk_path(header.doc_id)          # type: ignore[attr-defined]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for model in [header, *chunks]:
            f.write(json.dumps(model.model_dump(exclude_none=True), ensure_ascii=False) + "\n")
    return path


def chunk_files() -> list[Path]:
    config.require_data_dir()
    return sorted(config.CHUNK_DIR.glob("*.jsonl"))


# ---------------------------------------------------------------- 6단계: eval/ (RAG-024 ④)
def eval_path(model_key: str) -> Path:
    return config.EVAL_DIR / f"{model_key}.jsonl"


def write_eval(header: BaseModel, items: list[BaseModel]) -> Path:
    """1행 헤더 + 2행부터 문항. parsed·chunks 와 같은 파일 모양이라 `read()` 를 그대로 쓴다.

    **여기 쓰는 것은 판단이 아니라 물증이다.** 판정 요약은 이 파일이 아니라 RAG-024 로 간다 —
    이 덤프는 미추적이고 재수집하면 `chunk_id` 가 바뀌어 통째로 낡기 때문이다 (RAG-024 ④).
    """
    path = eval_path(header.model_key)        # type: ignore[attr-defined]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for model in [header, *items]:
            f.write(json.dumps(model.model_dump(exclude_none=True), ensure_ascii=False) + "\n")
    return path



# ---------------------------------------------------------------- 9단계: answers/ (RAG-028 ⑥)
def answer_path(stem: str = "lap1") -> Path:
    """1랩 덤프. 랩 이름이 파일명이다 — 2랩은 `lap2` 로 나란히 놓여 **대조가 파일 두 개 여는 일**이 된다."""
    return config.ANSWER_DIR / f"{stem}.jsonl"


def write_answers(header: BaseModel, items: list[BaseModel], stem: str = "lap1") -> Path:
    """`write_eval` 과 같은 파일 모양(1행 헤더 + 문항). 다른 함수인 것은 경로와 헤더 타입뿐이다.

    **여기 쓰는 것도 판단이 아니라 물증이다** (RAG-024 ④ 와 같은 규약). 1랩 요약 — 코퍼스 스냅샷 ·
    검문소④ 수치 · 문항별 한 줄 — 은 이 파일이 아니라 RAG-028 ⑥ 으로 간다. 이 덤프는 미추적이라
    **PC 를 갈아타면 사라지고**, 그러면 2랩에서 비교할 근거가 ADR 에만 남기 때문이다.
    """
    path = answer_path(stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for model in [header, *items]:
            f.write(json.dumps(model.model_dump(exclude_none=True), ensure_ascii=False) + "\n")
    return path

def read_chunks(path: Path) -> Iterator[dict[str, Any]]:
    """헤더를 건너뛰고 청크 행만. 4단계 임베더는 파일 경계를 무시하고 이것만 이어 붙인다."""
    for row in read(path):
        if row.get("type") == "chunk":
            yield row
