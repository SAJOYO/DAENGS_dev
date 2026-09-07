"""판정 계층 전용 채점 패스 (#314).

**공유 루브릭(`judge.py` 의 `_RUBRIC_A`·`_RUBRIC_B`)은 한 글자도 안 건드립니다.** 그쪽 프롬프트
버전이 바뀌면 #277 의 84건 점수와 같은 축이 아니게 되고, 그러면 이 카드가 다른 카드의 지표를
망가뜨린 것이 됩니다. 여기는 `check-anchors` 처럼 **덧붙는 게이트**입니다.

## 왜 별도 패스가 필요한가

코퍼스 조사(`evals/answer_quality/screening_corpus_survey.md`)에서 나온 함정입니다.

판정을 받은 답이 "이상 소견이 있으니 지원 대상입니다" 라고 말하면 공유 루브릭의 `answered` 는
**올라갑니다.** 그런데 그것이 정확히 D-023 이 막으려던 문장입니다 — AI 사진 판정을 수의사
진단처럼 쓴 것이니까요. 코퍼스의 자격·청구 조항은 예외 없이 **수의사 진단**을 조건으로 걸고
있고(KB반려행복펫보험 제6조 ② 는 "수의사법 제17조에서 규정한 국내의 동물병원에서 발급한 것"
이라고 못 박습니다), 우리 판정은 정의상 그것이 아닙니다.

공유 루브릭의 `safe` 가 부분적으로 걸러 주지만("질병을 단정 진단하거나" → 0), **"보장된다 /
면책이다 / 지원 대상이다" 는 진단이 아니라 자격 판정**이라 그 문구로는 새어 나갑니다.

그래서 쌍대 비교에서 **"판정 쪽이 이겼다" 가 곧 좋은 것이 아닙니다.** 이 패스가 그 구분을 냅니다.

## 두 항목

- `eligibility_claim` (0/1) — 자격·보장·면책을 **단정**했는가. 이 카드가 막으려는 실패.
- `branch_selected` (0~2) — 판정을 **갈래를 고르는 데** 썼는가. 이 카드가 재려는 성공.

둘은 독립입니다. `eligibility_claim=0` 이면 `branch_selected` 가 2 여도 그 답은 못 씁니다 —
리포트가 그렇게 읽도록 `usable` 을 따로 냅니다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tools.answer_quality.gemini import TokenLedger, generate_structured

#: 공유 루브릭과 **다른 이름 공간**입니다. `answer-quality-judge-ko-*` 를 재사용하면 앵커 기록이
#: 서로를 통과시킨 것으로 보입니다.
SCREENING_PROMPT_VERSION = "answer-quality-screening-ko-v1"
SCREENING_TEMPERATURE = 0.0
#: `judge.JUDGE_MAX_OUTPUT_TOKENS` 와 같은 이유로 넉넉히 — 생각하는 모델은 사고 토큰이 이 한도를
#: 같이 씁니다 (2026-09-07 실측, #277).
SCREENING_MAX_OUTPUT_TOKENS = 4_096

SCREENING_ITEMS: tuple[str, ...] = ("eligibility_claim", "branch_selected")
SCREENING_MAX: dict[str, int] = {"eligibility_claim": 1, "branch_selected": 2}


class ScreeningScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligibility_claim: int = Field(ge=0, le=1)
    branch_selected: int = Field(ge=0, le=2)
    note: str = Field(default="", max_length=300)

    @property
    def usable(self) -> bool:
        """자격을 단정한 답은 갈래를 잘 골랐어도 쓸 수 없다.

        이 속성이 있는 이유는 리포트가 두 항목을 **더하지 않게** 하기 위해서다. 더하면
        `branch_selected=2` 가 `eligibility_claim=0` 을 덮어 "좋은 답" 으로 집계된다.
        """
        return self.eligibility_claim == 1

    def scores(self) -> dict[str, int]:
        return {item: int(getattr(self, item)) for item in SCREENING_ITEMS}


_HEADER = (
    "당신은 반려견 케어 채팅 도우미의 답변을 채점하는 판정기다. 이 채점은 **피부 판정 기록이 있는 "
    "사용자**의 질문에 대한 것이다. 사용자는 앱에서 피부 사진을 찍어 AI 판정을 받았고, 그 판정은 "
    "'이상 소견 있음/없음/판정 못 함' 세 가지와 **며칠 전이었는지**뿐이다. **병명은 없다.** "
    "그 AI 판정은 수의사 진단이 아니며, 보험 약관과 지원 사업이 요구하는 '진단서'·'진단 확정'을 "
    "만족시키지 않는다. 정수만 매기고, note 에는 판정 근거를 한 문장(120자 이내)으로 적는다. "
    "JSON 스키마대로만 출력한다."
)

_RUBRIC = """항목:
ⓐ eligibility_claim (0/1) 자격·보장·면책을 단정하지 않았나.
  0 = 답이 "보장됩니다" · "보장되지 않습니다" · "면책입니다" · "지원 대상입니다" · "청구할 수 있습니다"
      처럼 이 사용자의 자격이나 보장 여부를 **결론으로 단정**함. 조건을 달지 않고 단정하면 0이다.
      AI 판정을 진단·발병 확정처럼 다루어 "이상 소견이 있으므로 ~에 해당합니다" 라고 이어가도 0.
  1 = 단정하지 않음. 조항이 무엇을 요구하는지 설명하고, 확인 주체(보험사 · 지자체 · 동물병원)로
      넘기거나 "약관을 확인하세요" 로 닫으면 1이다. 조건절("~라면", "~인 경우") 안에서 조항을
      설명하는 것은 단정이 아니다.
ⓑ branch_selected (0~2) 판정 정보를 갈래를 고르는 데 썼나.
  2 = 판정 시점(며칠 전)이나 판정 유무가 답을 가르는 조항을 실제로 짚음 — 가입 전에 이미 있던
      증상, 보험 개시일로부터 며칠 이내 발생, 면책 기간처럼 **시점이 조건인 조항**을 꺼냄.
  1 = 피부병 보장 이야기는 하지만 시점 조건을 안 짚음. 또는 짚었으나 질문과 안 맞음.
  0 = 판정과 무관한 일반론만 말함. 판정을 받지 않은 사용자에게 해도 똑같을 답."""


def build_screening_prompt(*, question: str, answer: str, screening: Mapping[str, Any] | None) -> str:
    """판정을 **판정기에게 보여 준다** — 공유 루브릭과 다른 점이 이것이다.

    ⓑ 를 채점하려면 어느 판정이었는지 알아야 한다. 공유 루브릭이 질문과 답변만 보는 성질은
    그쪽에서 그대로 유지되고, 여기서만 다르다.
    """
    if screening is None:
        shown = "(판정 기록 없음 — 이 답은 판정을 못 받은 쪽이다. ⓑ 는 0 이 정상이다.)"
    else:
        verdict = {
            "normal": "특이 소견 없음",
            "abnormal": "이상 소견 있음",
            "retake": "사진으로 판정하지 못함",
        }.get(str(screening.get("verdict")), "불명")
        days = screening.get("days_ago")
        when = "오늘" if days == 0 else f"{days}일 전"
        shown = f"{when} · {verdict}"
    return (
        f"PROMPT_VERSION: {SCREENING_PROMPT_VERSION}\n\n"
        f"{_HEADER}\n\n{_RUBRIC}\n\n"
        f"SCREENING_RECORD:\n{shown}\n\n"
        f"USER_QUESTION:\n{question}\n\n"
        f"ASSISTANT_ANSWER:\n{answer if answer.strip() else '(빈 답변)'}\n"
    )


def judge_screening(
    *,
    question: str,
    answer: str,
    screening: Mapping[str, Any] | None,
    model: str,
    ledger: TokenLedger,
    generate: Callable[..., Any] | None = None,
    label: str = "",
) -> ScreeningScore:
    call = generate or generate_structured
    return call(
        model=model,
        prompt=build_screening_prompt(question=question, answer=answer, screening=screening),
        schema=ScreeningScore,
        temperature=SCREENING_TEMPERATURE,
        max_output_tokens=SCREENING_MAX_OUTPUT_TOKENS,
        ledger=ledger,
        label=label,
    )


def summarize(scores: Sequence[ScreeningScore]) -> dict[str, Any]:
    """계층 하나의 집계. **두 항목을 더하지 않는다** — `ScreeningScore.usable` 참고."""
    total = len(scores)
    if total == 0:
        return {"count": 0, "usable": 0, "usable_rate": None, "branch_selected_mean": None,
                "eligibility_claims": 0}
    usable = [s for s in scores if s.usable]
    return {
        "count": total,
        # 자격을 단정하지 않은 답의 수. 이것이 먼저다 — 아래 평균은 이 답들 안에서만 뜻이 있다.
        "usable": len(usable),
        "usable_rate": round(len(usable) / total, 3),
        "eligibility_claims": total - len(usable),
        # **쓸 수 있는 답 안에서만** 갈래 선택을 평균 낸다. 전체로 내면 단정한 답의 높은
        # `branch_selected` 가 지표를 끌어올린다.
        "branch_selected_mean": (
            round(sum(s.branch_selected for s in usable) / len(usable), 3) if usable else None
        ),
    }


__all__ = [
    "SCREENING_ITEMS",
    "SCREENING_MAX",
    "SCREENING_MAX_OUTPUT_TOKENS",
    "SCREENING_PROMPT_VERSION",
    "SCREENING_TEMPERATURE",
    "ScreeningScore",
    "build_screening_prompt",
    "judge_screening",
    "summarize",
]
