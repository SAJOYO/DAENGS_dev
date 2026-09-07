"""동결 질문 파일의 스키마 · 로더 · 중복 제거 (#277).

`generate_questions.py` 가 쓰고 `collect.py` · `report.py` 가 읽는다. 생성기는 모델을 부르므로
읽는 쪽이 그 모듈을 import 하지 않게 여기로 뺐다 — 라우팅 골드의 `schemas.py` 와 같은 자리다.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tools.answer_quality.strata import STRATA_BY_ID

BACKEND_DIR = Path(__file__).resolve().parents[2]
ASSETS_DIR = BACKEND_DIR / "evals" / "answer_quality"
QUESTIONS_V1_PATH = ASSETS_DIR / "questions_v1.jsonl"

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


class QuestionCase(BaseModel):
    """질문 한 건. 필드 순서가 파일의 열 순서다."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    query: str = Field(min_length=1, max_length=1_000)
    context: dict[str, Any]
    stratum: str
    generator_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def stratum_is_defined_and_context_matches(self) -> QuestionCase:
        stratum = STRATA_BY_ID.get(self.stratum)
        if stratum is None:
            raise ValueError(f"unknown stratum {self.stratum!r}")
        if self.context != stratum.context():
            raise ValueError(f"{self.question_id}: context does not match stratum {self.stratum}")
        if not self.question_id.startswith(f"{self.stratum}_"):
            raise ValueError(f"{self.question_id}: question_id must be prefixed by its stratum")
        return self


def normalized_key(text: str) -> str:
    """중복 판정 키. 유니코드 정규화 · 대소문자 · 공백 · 문장부호를 지우고 남는 글자만 본다.

    "강아지 사료 얼마나 줘야해요?" 와 "강아지사료 얼마나 줘야 해요" 는 같은 질문이다.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _NON_WORD.sub("", folded)


def dedupe(queries: Iterable[str], *, seen: set[str] | None = None) -> list[str]:
    """정규화 키가 처음 나온 문장만 남긴다. `seen` 을 주면 그 키들도 이미 나온 것으로 본다."""
    keys = set() if seen is None else seen
    kept: list[str] = []
    for query in queries:
        key = normalized_key(query)
        if not key or key in keys:
            continue
        keys.add(key)
        kept.append(query)
    return kept


def load_questions(path: Path = QUESTIONS_V1_PATH) -> list[QuestionCase]:
    """검증하며 읽는다 — id 와 정규화 키가 파일 전체에서 유일해야 한다."""
    cases: list[QuestionCase] = []
    ids: set[str] = set()
    keys: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        case = QuestionCase.model_validate(raw)
        if case.question_id in ids:
            raise ValueError(f"duplicate question_id {case.question_id} at {path}:{line_number}")
        key = normalized_key(case.query)
        if key in keys:
            raise ValueError(f"duplicate query (normalized) at {path}:{line_number}")
        ids.add(case.question_id)
        keys.add(key)
        cases.append(case)
    return cases


def write_questions(path: Path, cases: Iterable[QuestionCase]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case.model_dump(mode="json"), ensure_ascii=False) + "\n")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "ASSETS_DIR",
    "BACKEND_DIR",
    "QUESTIONS_V1_PATH",
    "QuestionCase",
    "dedupe",
    "file_sha256",
    "load_questions",
    "normalized_key",
    "write_questions",
]
