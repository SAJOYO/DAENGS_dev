"""OpenAI 구조화 출력 한 번 + 토큰 장부 — 레포에 없던 조각.

`answer_quality/gemini.py` 는 장부가 있지만 Gemini 전용이고, `training_quality/judge.py` 는
OpenAI 를 부르지만 `usage` 를 안 읽는다. 여기서 둘을 합친다: 같은 `TokenLedger` 에 적고, 한 단계의
예산을 넘으면 멈춘다. 스키마 실패에만 **한 번** 더 부른다 (O-14 와 같은 계약). 프로바이더 실패는
그대로 올린다 — 조용히 통과로 읽히면 안 된다.

**사고 토큰은 출력으로 센다.** `output_tokens_details.reasoning_tokens` 는 출력으로 과금되고,
Gemini 쪽에서 `thoughts_token_count` 를 출력에 합친 것과 같은 이유다 — 예산이 실제 비용을 보게.

**키는 `settings` 에서 명시적으로 넘긴다.** `backend/.env` 는 `os.environ` 에 오르지 않으므로 SDK
기본 생성자에 맡기면 개발 PC 에서 키를 못 찾는다 (`config.py` 주석).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from daengs_evals.answer_quality.gemini import SchemaOutputError, TokenLedger


def client(api_key: str | None = None, *, timeout_s: float | None = None) -> Any:
    from openai import OpenAI

    from daengs_backend.config import settings

    key = api_key or settings.openai_api_key.get_secret_value().strip()
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY 가 없습니다 — backend/.env 를 확인하세요 (루트 .env 가 아닙니다). "
            "판정기는 생성과 다른 계열을 씁니다 (calibration/hygiene.py)."
        )
    return OpenAI(api_key=key, timeout=timeout_s or settings.openai_timeout_s)


def _usage(resp: Any) -> tuple[int | None, int | None]:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None, None
    output = int(getattr(usage, "output_tokens", None) or 0)
    details = getattr(usage, "output_tokens_details", None)
    reasoning = int(getattr(details, "reasoning_tokens", None) or 0) if details else 0
    # reasoning 이 output 에 이미 포함된 SDK 도 있고 아닌 것도 있다 — 큰 쪽을 쓴다. 예산은 상한이다.
    return getattr(usage, "input_tokens", None), max(
        output, output + reasoning - min(reasoning, output)
    )


def generate_structured[ModelT: BaseModel](
    *,
    model: str,
    prompt: str,
    schema: type[ModelT],
    temperature: float,
    ledger: TokenLedger,
    cli: Any = None,
    label: str = "",
) -> ModelT:
    """구조화 출력 한 번. 스키마 실패에만 한 번 더 부른다."""
    active = cli or client()
    last_error: Exception | None = None
    for _attempt in range(2):
        resp = active.responses.parse(
            model=model, input=prompt, text_format=schema, temperature=temperature
        )
        input_tokens, output_tokens = _usage(resp)
        ledger.add(input_tokens=input_tokens, output_tokens=output_tokens, label=label)
        parsed = getattr(resp, "output_parsed", None)
        if parsed is not None:
            return parsed
        # SDK 가 파싱을 못 했을 때 — 원문을 한 번 더 검증해 본다 (모델 검증기가 거부했을 수 있다)
        raw = getattr(resp, "output_text", None)
        if raw:
            try:
                return schema.model_validate_json(raw)
            except ValidationError as exc:
                last_error = exc
                continue
        last_error = RuntimeError("output_parsed 도 output_text 도 없다")
    raise SchemaOutputError(f"{model}: 두 번 다 스키마를 못 지켰다 — {last_error}")


__all__ = ["client", "generate_structured"]
