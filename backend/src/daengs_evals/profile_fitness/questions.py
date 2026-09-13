"""반사실 질문 파일의 스키마 · 로더 · 검증.

질문 하나가 곧 실험 하나다 — 어떤 질문을 어떤 두 프로필로 물을지, 그리고 **답이 갈려야
하는지 같아야 하는지**가 여기 적힌다. 그 기대(`kind`)는 우리 골드이고 **판정 프롬프트에는
절대 들어가지 않는다** (`rubric.build_prompt`).

세 종류:

    reactive    프로필이 답을 갈라야 한다   — 산책량 · 사료량 · 계단
    invariant   프로필이 답을 가르면 안 된다 — 꼬리 흔드는 뜻 · 사료 보관법
    probe       프로필 없이 물어 **없는 기록을 지어내는지** 본다 — "지난번 검사 결과 보면…"

`tier` 는 난이도 계층이다. 크게 갈려야 하는 것(coarse)만 담으면 민감도가 부풀려지고, 미세한
것(fine)만 담으면 지표가 아무것도 못 잡는다. 둘을 나눠 담고 리포트가 나눠 센다.

**질문의 저자는 사람(팀)과 Claude 다.** 답변 생성은 Gemini, 판정은 OpenAI 라 셋이 전부
다른 계열이다 — 생성기가 자기가 답하기 쉬운 질문을 내고 판정기가 그것을 채점하는 순환을
막는다. 실사용 질문 문장은 베끼지 않는다 (`evals/orchestration_router/README.md`).

실측(2026-09-09, 프로필 두 개 × 후보 다섯 문항)에서 일상 돌봄 질문은 **전부 `general` 로
갔고**, `general` 폴백이 꺼져 있으면 전부 FAILED("반려견에 관한 질문만…") 였다. 그래서 수집은
`--flag on` 이 필수이고, v1 의 측정 범위는 `general` 이다.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from daengs_evals.answer_quality.questions import normalized_key
from daengs_evals.profile_fitness.profiles import ASSETS_DIR, Profile, profiles_by_id
from daengs_evals.profile_fitness.rubric import Kind

QUESTIONS_V1_PATH = ASSETS_DIR / "questions_pf_v1.jsonl"

Tier = Literal["coarse", "fine"]

#: 절제군(프로필 없음)의 profile_id. 픽스처 파일과 같은 이름이어야 한다.
ABSENT_ARM = "none"

#: 조건 P(절제)에서 기준이 되는 위치. `arms[0]` 이 baseline 이다.
BASELINE = 0


class Question(BaseModel):
    """질문 한 건. 필드 순서가 파일의 열 순서다."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^pf_[a-z][a-z0-9_]*_\d{2}$")
    query: str = Field(min_length=1, max_length=300)
    kind: Kind
    tier: Tier
    #: 비교할 프로필 둘. `arms[0]` 이 baseline. probe 는 `arms[0] == "none"` 이어야 한다.
    arms: list[str] = Field(min_length=2, max_length=2)
    #: 왜 갈려야(또는 안 갈려야) 하는가 — **리뷰 메타. 프롬프트에 안 들어간다.**
    sensitive_to: list[str] = Field(default_factory=list)
    #: 같은 뜻의 다른 문장이면 원 질문 id. 안정성 시험(패러프레이즈)용.
    paraphrase_of: str | None = None
    #: 저자. 사람이 쓴 것과 모델이 쓴 것을 갈라 둔다.
    author: str = Field(min_length=1)

    @model_validator(mode="after")
    def arms_match_kind(self) -> Question:
        if self.arms[0] == self.arms[1]:
            raise ValueError(f"{self.question_id}: 두 arm 이 같다 — 대조군은 수집기가 만든다")
        if self.kind == "probe" and self.arms[0] != ABSENT_ARM:
            raise ValueError(f"{self.question_id}: probe 는 arms[0] 이 {ABSENT_ARM!r} 여야 한다")
        if self.kind != "probe" and ABSENT_ARM in self.arms:
            raise ValueError(
                f"{self.question_id}: {self.kind} 에 {ABSENT_ARM!r} 을 직접 쓰지 않는다 — "
                "절제 조건은 수집기가 붙인다"
            )
        if self.kind == "invariant" and self.sensitive_to:
            raise ValueError(f"{self.question_id}: invariant 는 sensitive_to 가 비어야 한다")
        if self.kind == "reactive" and not self.sensitive_to:
            raise ValueError(f"{self.question_id}: reactive 는 무엇에 갈리는지 적어야 한다")
        return self


def load_questions(path: Path | str = QUESTIONS_V1_PATH) -> list[Question]:
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    questions = [Question.model_validate(row) for row in rows]
    _check_ids(questions)
    _check_duplicates(questions)
    _check_paraphrase_targets(questions)
    return questions


def _check_ids(questions: Iterable[Question]) -> None:
    ids = Counter(q.question_id for q in questions)
    dupes = sorted(k for k, n in ids.items() if n > 1)
    if dupes:
        raise ValueError(f"question_id 중복: {dupes}")


def _check_duplicates(questions: Iterable[Question]) -> None:
    """같은 문장이 두 번 있으면 같은 실험을 두 번 세는 것이다. 패러프레이즈는 예외가 아니다 —
    같은 문장이면 패러프레이즈가 아니다."""
    seen: dict[str, str] = {}
    for q in questions:
        key = normalized_key(q.query)
        if key in seen:
            raise ValueError(f"{q.question_id} 와 {seen[key]} 가 같은 질문이다")
        seen[key] = q.question_id


def _check_paraphrase_targets(questions: list[Question]) -> None:
    ids = {q.question_id for q in questions}
    for q in questions:
        if q.paraphrase_of is None:
            continue
        if q.paraphrase_of not in ids:
            raise ValueError(f"{q.question_id}: paraphrase_of {q.paraphrase_of!r} 가 없다")
        origin = next(o for o in questions if o.question_id == q.paraphrase_of)
        if origin.kind != q.kind or origin.arms != q.arms:
            raise ValueError(
                f"{q.question_id}: 패러프레이즈는 원 질문과 kind · arms 가 같아야 한다"
            )


def check_against_profiles(questions: Iterable[Question], profiles: Iterable[Profile]) -> list[str]:
    """질문이 가리키는 arm 이 픽스처에 있는지, 두 arm 이 같은 능력에 닿는지.

    두 arm 의 `visible_to` 가 겹치지 않으면 어느 능력이 답해도 한쪽은 범위 밖이라 그 질문은
    영원히 미측정이다. 그건 데이터 오류이지 결과가 아니다.
    """
    by_id = profiles_by_id(profiles)
    problems: list[str] = []
    for q in questions:
        for arm in q.arms:
            if arm not in by_id:
                problems.append(f"{q.question_id}: 프로필 {arm!r} 이 없다")
        if all(a in by_id for a in q.arms):
            reach = set(by_id[q.arms[0]].visible_to) & set(by_id[q.arms[1]].visible_to)
            if not reach:
                problems.append(f"{q.question_id}: 두 arm 이 같은 능력에 닿지 않는다")
    return problems


def summarize(questions: Iterable[Question]) -> dict[str, Any]:
    qs = list(questions)
    return {
        "count": len(qs),
        "by_kind": dict(Counter(q.kind for q in qs)),
        "by_tier": dict(Counter(q.tier for q in qs)),
        "paraphrases": sum(1 for q in qs if q.paraphrase_of),
        "authors": dict(Counter(q.author for q in qs)),
    }


__all__ = [
    "ABSENT_ARM",
    "BASELINE",
    "QUESTIONS_V1_PATH",
    "Question",
    "check_against_profiles",
    "load_questions",
    "summarize",
]
