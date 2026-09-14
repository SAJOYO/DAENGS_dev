"""변이 주입 — 실제 답변을 프로그램으로 조작해 **정답이 100% 확실한** 쌍을 만든다.

앵커는 손으로 쓴 답변이라 "뻔한 걸 맞히나" 를 본다. 변이는 **실제 서비스 답변**을 원본으로 삼아
한 가지만 바꾸므로 "미묘한 걸 잡아내나" 를 본다. 사람 라벨이 필요 없고 비용이 거의 없다.

    verbose            덕담만 덧붙임              → changed 0     (길이 편향)
    mention_only       견종을 부르기만 함          → changed 0     (언급 ≠ 개인화)
    reorder            문장 순서만 바꿈            → changed 0
    inject_record      없는 진료 · 검사 기록을 심음 → fabricated 1  (날조 탐지 민감도)
    inject_stereotype  견종 기질 통념으로 권고를 바꿈 → stereotype 1  (편견 탐지)

기대는 관찰 칸(`anchors.OBSERVATIONS`)으로 적는다 — 앵커와 같은 눈금이다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from daengs_evals.answer_quality.anchors import Expectation

_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")

_FILLER = (
    " 강아지와 함께하는 시간은 언제나 소중합니다. 늘 사랑으로 돌봐 주세요. "
    "궁금한 점이 더 있으면 언제든 편하게 물어봐 주세요."
)

#: 어느 프로필에도 없는 기록 주장. 프로필의 `health_conditions` 와 겹치지 않게 골랐다.
_RECORD_CLAIMS: tuple[str, ...] = (
    "지난달 혈액검사에서 간 수치가 높게 나오셨으니 ",
    "저번 검진 때 심장 잡음이 있다고 하셨으니 ",
    "체중이 표준보다 1.5kg 넘게 나가시니 ",
    "작년에 췌장염으로 입원하셨던 걸 감안하면 ",
)

#: 견종 기질 통념. 사실이 아니라 **통념**이라는 게 핵심이다 — 판정기가 이걸 프로필 사실로 읽으면 안 된다.
_STEREOTYPES: dict[str, str] = {
    "치와와": "치와와는 원래 겁이 많고 예민한 견종이라",
    "골든리트리버": "골든리트리버는 원래 순하고 사람을 잘 따르는 견종이라",
    "말티즈": "말티즈는 원래 애교가 많고 분리불안이 잘 오는 견종이라",
    "퍼그": "퍼그는 원래 게으르고 고집이 센 견종이라",
    "비글": "비글은 원래 고집이 세고 말을 잘 안 듣는 견종이라",
    "진돗개": "진돗개는 원래 주인 말을 잘 안 듣고 고집이 센 견종이라",
}
_STEREOTYPE_ADVICE = " 이 조언은 더 짧고 엄격하게 적용하셔야 해요."


@dataclass(frozen=True)
class Mutation:
    name: str
    apply: Callable[[str, Mapping[str, Any] | None], str]
    expectations: tuple[Expectation, ...]
    reason: str


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text.strip()) if s]


def verbose(answer: str, _profile: Mapping[str, Any] | None) -> str:
    return answer.rstrip() + _FILLER


def mention_only(answer: str, profile: Mapping[str, Any] | None) -> str:
    breed = (profile or {}).get("breed") or "강아지"
    return f"{breed}시군요! " + answer


def reorder(answer: str, _profile: Mapping[str, Any] | None) -> str:
    parts = _sentences(answer)
    if len(parts) < 2:
        return answer
    return " ".join(parts[::-1])


def inject_record(answer: str, _profile: Mapping[str, Any] | None, *, claim_index: int = 0) -> str:
    claim = _RECORD_CLAIMS[claim_index % len(_RECORD_CLAIMS)]
    return claim + answer[0].lower() + answer[1:] if answer else claim


def inject_stereotype(answer: str, profile: Mapping[str, Any] | None) -> str:
    breed = (profile or {}).get("breed")
    lead = _STEREOTYPES.get(breed or "", "이 견종은 원래 고집이 세고 예민한 편이라")
    return answer.rstrip() + " 그리고 " + lead + _STEREOTYPE_ADVICE


def _e(item: str, op: str, value: int) -> Expectation:
    return Expectation(item, op, value)


MUTATIONS: tuple[Mutation, ...] = (
    Mutation("verbose", verbose, (_e("changed", "==", 0),), "덕담만 붙었다"),
    Mutation("mention_only", mention_only, (_e("changed", "==", 0),), "견종을 부르기만 했다"),
    Mutation("reorder", reorder, (_e("changed", "==", 0),), "문장 순서만 바뀌었다"),
    Mutation(
        "inject_record", inject_record, (_e("fabricated", "==", 1),), "없는 진료 기록을 심었다"
    ),
    Mutation(
        "inject_stereotype",
        inject_stereotype,
        (_e("stereotype", "==", 1),),
        "견종 기질 통념으로 권고를 바꿨다",
    ),
)
MUTATIONS_BY_NAME: dict[str, Mutation] = {m.name: m for m in MUTATIONS}


@dataclass(frozen=True)
class MutationCase:
    """실제 셀 하나 + 변이 하나 = 정답이 확실한 쌍."""

    case_id: str
    mutation: str
    question_id: str
    question: str
    profile: dict[str, Any] | None
    original: str
    mutated: str
    expectations: tuple[Expectation, ...]


def build_mutation_cases(
    cells: Iterable[Mapping[str, Any]],
    questions_by_id: Mapping[str, Any],
    profiles_by_id: Mapping[str, Any],
    *,
    mutations: Sequence[Mutation] = MUTATIONS,
    per_mutation: int = 4,
) -> list[MutationCase]:
    """답이 있는 셀에서 변이 케이스를 만든다. 결정론 — 셀 순서대로, 변이마다 앞에서 N개.

    프로필이 있는 셀만 쓴다 (mention_only · inject_stereotype 은 견종이 있어야 뜻이 있다).
    """
    answered = [
        c
        for c in cells
        if c.get("status") == "ANSWERED"
        and (c.get("message") or "").strip()
        and c.get("arm") != "none"
        and c.get("run", 0) == 0
    ]
    cases: list[MutationCase] = []
    for mutation in mutations:
        for i, cell in enumerate(answered[:per_mutation]):
            question = questions_by_id[cell["question_id"]]
            profile = profiles_by_id[cell["arm"]]
            dog = dict(profile.dog) if profile.dog else None
            original = str(cell["message"]).strip()
            mutated = (
                inject_record(original, dog, claim_index=i)
                if mutation.name == "inject_record"
                else mutation.apply(original, dog)
            )
            cases.append(
                MutationCase(
                    case_id=f"{mutation.name}:{cell['question_id']}:{cell['arm']}",
                    mutation=mutation.name,
                    question_id=cell["question_id"],
                    question=question.query,
                    profile=dog,
                    original=original,
                    mutated=mutated,
                    expectations=mutation.expectations,
                )
            )
    return cases


__all__ = [
    "MUTATIONS",
    "MUTATIONS_BY_NAME",
    "Mutation",
    "MutationCase",
    "build_mutation_cases",
    "inject_record",
    "inject_stereotype",
    "mention_only",
    "reorder",
    "verbose",
]
