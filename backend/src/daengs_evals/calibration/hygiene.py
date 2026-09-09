"""판정 모델 위생 — 주석으로만 있던 규칙 둘을 실행 가능하게.

ⓐ **계열 분리.** 생성이 Gemini 면 판정은 다른 계열이어야 한다. 같은 훈련 계보가 자기 계열
   문장을 후하게 보는 self-preference 는 급(flash → pro)을 갈라도 남는다 — 2026-09-07 결정,
   `config.py` `openai_judge_model` 주석.
ⓑ **날짜 핀.** `-latest` 류의 움직이는 이름을 판정기에 쓰지 않는다. 두 판정 파일의 차이가
   답변 때문인지 judge 때문인지 안 갈린다 (#305).

둘 다 **판정기를 만들기 전에** 검사한다. 잘못된 모델로 한 판정을 나중에 걸러내는 것보다 처음부터
못 만들게 하는 편이 싸다.
"""

from __future__ import annotations

import re

#: 모델 이름 앞머리 → 계열. 여기 없는 이름은 계열을 모른다고 본다 (통과시키지 않는다).
_FAMILY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("gemini", "google"),
    ("gemma", "google"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude", "anthropic"),
)

_DATE_PIN = re.compile(r"\d{4}-\d{2}-\d{2}$")
_MOVING_SUFFIXES = ("-latest", "-preview", "-exp")


class JudgeHygieneError(RuntimeError):
    """판정 모델이 위생 규칙을 어긴다. 판정기를 만들지 않는다."""


def model_family(model: str) -> str:
    name = model.strip().lower()
    for prefix, family in _FAMILY_PREFIXES:
        if name.startswith(prefix):
            return family
    raise JudgeHygieneError(
        f"계열을 모르는 모델 이름입니다: {model!r} — hygiene._FAMILY_PREFIXES 에 추가하세요"
    )


def require_family_split(judge_model: str, generation_models: list[str] | tuple[str, ...]) -> str:
    """판정 계열이 생성 계열 어느 것과도 같지 않아야 한다. 판정 계열을 돌려준다."""
    judge = model_family(judge_model)
    same = [m for m in generation_models if model_family(m) == judge]
    if same:
        raise JudgeHygieneError(
            f"판정 {judge_model!r} 이 생성 {same} 과 같은 계열({judge})입니다 — "
            "자기편애가 남습니다. 다른 계열을 쓰세요 (2026-09-07 결정)."
        )
    return judge


def require_pinned_model(model: str) -> str:
    """날짜가 박힌 이름만 통과. `-latest` · `-preview` · `-exp` 는 거부."""
    name = model.strip()
    if any(name.endswith(s) for s in _MOVING_SUFFIXES):
        raise JudgeHygieneError(
            f"움직이는 이름입니다: {model!r} — 날짜가 박힌 이름을 쓰세요 (#305)"
        )
    if not _DATE_PIN.search(name):
        raise JudgeHygieneError(f"날짜 핀이 없습니다: {model!r} — 예: gpt-5.4-2026-03-05")
    return name


def require_judge_hygiene(
    judge_model: str, generation_models: list[str] | tuple[str, ...]
) -> dict[str, str]:
    """둘 다. 판정기 생성 직전에 부른다."""
    return {
        "judge_model": require_pinned_model(judge_model),
        "judge_family": require_family_split(judge_model, generation_models),
        "generation_families": ",".join(sorted({model_family(m) for m in generation_models})),
    }


__all__ = [
    "JudgeHygieneError",
    "model_family",
    "require_family_split",
    "require_judge_hygiene",
    "require_pinned_model",
]
