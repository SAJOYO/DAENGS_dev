"""Gemini 구조화 출력 호출 하나 + 토큰 장부 (#277).

생성기와 판정기가 같은 함수로 부른다 — 스키마 실패 시 **한 번만** 재시도(O-14 와 같은 계약),
프로바이더 실패는 그대로 올린다. 모든 호출이 `usage_metadata` 를 장부에 적고, 장부는 한 단계의
예산을 넘는 순간 멈춘다. 클라이언트는 의미 라우터의 것(`semantic._gemini_client`)을 그대로 쓴다 —
키 · 타임아웃을 두 곳에 적지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

#: 한 단계(명령 하나)의 기본 토큰 예산. 넘으면 `TokenBudgetExceeded` 로 멈추고 결과를 보고한다.
DEFAULT_TOKEN_BUDGET = 400_000

ModelT = TypeVar("ModelT", bound=BaseModel)


class TokenBudgetExceeded(RuntimeError):
    """한 단계의 토큰 합계가 예산을 넘었다. 부른 쪽이 여기서 멈추고 지금까지의 결과를 남긴다."""


class SchemaOutputError(RuntimeError):
    """재시도 한 번 뒤에도 출력이 스키마를 지키지 않았다."""


@dataclass
class TokenLedger:
    """한 명령의 누적 사용량. 호출마다 한 줄 찍고, 예산을 넘으면 멈춘다."""

    budget: int | None = DEFAULT_TOKEN_BUDGET
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    log: Callable[[str], None] | None = field(default=None, repr=False)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, *, input_tokens: int | None, output_tokens: int | None, label: str = "") -> None:
        self.calls += 1
        self.input_tokens += int(input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)
        if self.log is not None:
            self.log(
                f"    tokens +{int(input_tokens or 0)}/{int(output_tokens or 0)} "
                f"→ 누적 {self.total:,} (호출 {self.calls}){'  ' + label if label else ''}"
            )
        self.check()

    def check(self) -> None:
        if self.budget is not None and self.total > self.budget:
            raise TokenBudgetExceeded(
                f"토큰 합계 {self.total:,} 이 예산 {self.budget:,} 을 넘었습니다 — 여기서 멈춥니다"
            )

    def summary(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total,
            "budget": self.budget,
        }


def _client() -> Any:
    from daengs_backend.orchestration.semantic import _gemini_client

    return _gemini_client()


def generation_config(
    *,
    schema: type[BaseModel],
    temperature: float,
    max_output_tokens: int,
    seed: int | None = None,
) -> Any:
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=temperature,
        candidate_count=1,
        max_output_tokens=max_output_tokens,
        seed=seed,
        response_mime_type="application/json",
        response_json_schema=schema.model_json_schema(),
    )


def parse_structured(raw: object, schema: type[ModelT]) -> ModelT | None:
    """프로바이더 출력을 스키마로. 무효면 None — 원문은 올리지 않는다."""
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, schema):
        return parsed
    try:
        return schema.model_validate(parsed)
    except (ValidationError, ValueError, TypeError):
        return None


def generate_structured(
    *,
    model: str,
    prompt: str,
    schema: type[ModelT],
    temperature: float,
    max_output_tokens: int,
    ledger: TokenLedger,
    seed: int | None = None,
    client: Any = None,
    label: str = "",
) -> ModelT:
    """구조화 출력 한 번. 스키마 실패에만 한 번 더 부른다."""
    config = generation_config(
        schema=schema, temperature=temperature, max_output_tokens=max_output_tokens, seed=seed
    )
    active = client or _client()
    for _attempt in range(2):
        response = active.models.generate_content(model=model, contents=prompt, config=config)
        usage = getattr(response, "usage_metadata", None)
        ledger.add(
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            label=label,
        )
        raw = getattr(response, "parsed", None)
        if raw is None:
            raw = getattr(response, "text", None)
        result = parse_structured(raw, schema)
        if result is not None:
            return result
    raise SchemaOutputError(f"{model}: 출력이 두 번 다 {schema.__name__} 스키마를 지키지 않았습니다")


async def generate_structured_async(**kwargs: Any) -> Any:
    return await asyncio.to_thread(generate_structured, **kwargs)


__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "SchemaOutputError",
    "TokenBudgetExceeded",
    "TokenLedger",
    "generate_structured",
    "generate_structured_async",
    "generation_config",
    "parse_structured",
]
