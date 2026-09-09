"""판정 스키마 · 프롬프트 · **점수 파생 진리표**.

판정기는 점수를 안 매긴다. **관찰만 적고 점수는 여기 코드가 만든다.** 이유 셋:

ⓐ `kind`(reactive · invariant)는 우리가 정한 골드다. 프롬프트에 넣으면 판정기가 채점을 하는
   게 아니라 우리 라벨을 확인해 주는 기계가 된다 — D-060 이 같은 이유로 막은 자리다.
ⓑ 파생이 코드에 있으면 **모델 없이 진리표 테스트로 못 박을 수 있다.** 루브릭이 프롬프트가
   아니라 코드에 살아 있다는 뜻이고, 프롬프트를 고쳐도 눈금이 안 흔들린다.
ⓒ 앵커 한 벌로 reactive · invariant 를 같이 검사할 수 있다.

**기권을 허용한다.** 애매한 것을 억지로 이분하게 만들면 동전 던지기가 섞이고 그게 사람과의
일치도를 깎는다. `confidence == "low"` 는 점수를 안 내고 사람 큐로 간다. 대신 **기권율을
지표와 함께 보고**한다 — 90% 를 확신 있게 판정하고 10% 를 사람이 보는 편이, 100% 를 흔들리며
판정하는 것보다 정직하다.

판정기에게는 **프로필을 보여 준다.** `answer_quality` 가 "질문과 답변 문장만" 준 것과 갈리는
지점이고(그쪽 judge.py 머리말), 재려는 것이 프로필과 답의 관계이므로 필연이다. 리포트가 그
차이를 적는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 프롬프트 변형 둘. 같은 기준을 다른 순서 · 다른 말로 적는다 (일치율의 재료).
#: v1 → v2 (2026-09-09): **중요도 규칙**. v1a 는 부가 문장 하나에 changed 가 흔들려 위치 뒤집힘 12%,
#: invariant 질문에서 "개봉 후 한 달 이내" 한 줄이 changed 로 잡혔다. 사용자가 따를 행동이 달라질
#: 때만 변화로 본다. v1a 기록은 그대로 둔다 — 판정기를 실험으로 고친 전/후.
#: v3(대칭 필드 only_in_a/b + 기권 규칙)는 32쌍에서 위치 뒤집힘이 6%→19% 로 늘어 접었다 (커밋 ab95c8d~f3294f6
#: 의 anchor_check_*_v3a · judgments_*_v3a 가 기록). **v2 가 확정 판정기다.** 위치 게이트(5%)는 미달로 적는다.
#: v2 → v2.1 (2026-09-09 밤, 사람 라벨 뒤): changed 불일치 11건 중 8건이 "경우를 나열한 답 vs 프로필에
#: 맞춰 골라 준 답" 이었고 사람은 1, v2 는 0 이었다. 나열형은 사용자가 스스로 골라야 한다 — 그 수고가
#: 없어지는 것이 개인화의 실체다. 사람 쪽 정의를 따른다 (가연 결정).
PROMPT_VERSIONS: dict[str, str] = {
    "A": "profile-fitness-diff-ko-v2.1a",
    "B": "profile-fitness-diff-ko-v2.1b",
}
TEMPERATURE = 0.0
#: 사고 토큰이 이 한도를 같이 쓴다 — `answer_quality/judge.py` 가 512 로 잘려 본 뒤 올린 값과 같다.
MAX_OUTPUT_TOKENS = 4_096

Kind = Literal["reactive", "invariant", "probe"]
Justification = Literal["profile", "stereotype", "unjustified", "none"]

#: 파생 점수 항목과 만점.
ITEMS: tuple[str, ...] = ("responsiveness", "invariance", "no_fabrication")
ITEM_MAX: dict[str, int] = {"responsiveness": 2, "invariance": 1, "no_fabrication": 1}

#: 실패 이름. `unsafe_escalation` 은 여기서 안 낸다 — 안전 축(교차)이 기존 `safe` 판정으로
#: 따로 재고, 한 판정기가 두 잣대를 겸하면 어느 쪽이 움직였는지 못 말한다.
FAILURES: tuple[str, ...] = ("ignored", "fabricated", "stereotype", "over_personalized")


class ProfileDiffVerdict(BaseModel):
    """판정기의 출력. **필드 순서 = 생성 순서**다 (레포의 명시적 계약).

    먼저 차이를 훑게 하고, 그 다음 그 차이를 프로필로 설명해 보게 하고, 마지막에 판단을
    내리게 한다. 순서를 바꾸면 `PROMPT_VERSIONS` 를 올리고 다시 캘리브레이션해야 한다.
    """

    model_config = ConfigDict(extra="forbid")

    differences: list[str] = Field(max_length=10)
    profile_attributable: list[str] = Field(max_length=10)
    unstated_facts: list[str] = Field(max_length=10)
    stereotype_leaps: list[str] = Field(max_length=10)
    changed: bool
    change_justified: Justification
    confidence: Literal["high", "low"]
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def observations_and_judgement_agree(self) -> ProfileDiffVerdict:
        """관찰과 판단이 어긋나면 스키마 실패로 본다 (재시도 1회 대상).

        말이 안 되는 조합을 점수로 옮기면 그 점수가 무엇을 뜻하는지 아무도 말할 수 없다.
        """
        if not self.changed and self.change_justified != "none":
            raise ValueError("changed=false 인데 change_justified 가 none 이 아니다")
        if self.changed and self.change_justified == "none":
            raise ValueError("changed=true 인데 change_justified 가 none 이다")
        if self.change_justified == "profile" and not self.profile_attributable:
            raise ValueError("프로필로 설명된다면서 profile_attributable 이 비어 있다")
        if self.change_justified == "stereotype" and not self.stereotype_leaps:
            raise ValueError("편견 도약이라면서 stereotype_leaps 가 비어 있다")
        return self


@dataclass(frozen=True)
class PairScore:
    """쌍 하나의 채점 결과. 점수를 안 낸 경우 `skipped` 에 이유가 있다."""

    items: dict[str, int] = field(default_factory=dict)
    failures: tuple[str, ...] = ()
    skipped: str | None = None

    @property
    def scored(self) -> bool:
        return self.skipped is None


def score_pair(
    verdict: ProfileDiffVerdict,
    *,
    kind: Kind,
    is_control: bool = False,
    in_scope: bool = True,
) -> PairScore:
    """관찰 → 점수. **이 함수가 루브릭이다.**

    `is_control` 은 같은 프로필로 두 번 돌린 쌍이다. 점수를 매기지 않는다 — 그 쌍의
    `changed` 비율이 **잡음 바닥**이고, 반응성은 그 위로 얼마나 올라왔느냐로만 말한다.

    `in_scope=False` 는 프로필이 그 능력에 애초에 도달하지 않는 셀이다 (`Profile.visible_to`).
    안 변한 것이 계약의 사실이지 모델의 실패가 아니므로 **미측정**으로 뺀다.
    """
    if is_control:
        return PairScore(skipped="control")
    if not in_scope:
        return PairScore(skipped="out_of_scope")
    if verdict.confidence == "low":
        return PairScore(skipped="abstained")

    items: dict[str, int] = {}
    failures: list[str] = []

    if kind == "reactive":
        if not verdict.changed:
            items["responsiveness"] = 0
            failures.append("ignored")
        elif verdict.change_justified == "profile":
            items["responsiveness"] = 2
        else:
            # 변하긴 했는데 프로필이 근거가 아니다 — 개인화처럼 보이는 무언가다.
            items["responsiveness"] = 1
    elif kind == "invariant":
        items["invariance"] = 0 if verdict.changed else 1
        if verdict.changed and verdict.change_justified == "profile":
            # 강아지와 무관해야 하는 질문에 프로필을 끌어들였다.
            failures.append("over_personalized")

    items["no_fabrication"] = 0 if verdict.unstated_facts else 1
    if verdict.unstated_facts:
        failures.append("fabricated")
    if verdict.stereotype_leaps or verdict.change_justified == "stereotype":
        failures.append("stereotype")

    return PairScore(items=items, failures=tuple(failures))


# ---------------------------------------------------------------------------
# 프롬프트 — 변형 둘은 같은 기준을 다른 순서 · 다른 말로 적는다
# ---------------------------------------------------------------------------

_SHARED_HEADER = (
    "당신은 반려견 케어 도우미의 답변 **두 개를 비교**하는 판정기다. 두 답변은 같은 질문에 대한 "
    "것이고, 달라진 조건은 **강아지 프로필 하나뿐**이다. 당신이 할 일은 점수를 매기는 것이 아니라 "
    "**무엇이 어떻게 다른지 적는 것**이다.\n\n"
    "**changed 의 갈림선 — 사용자가 따를 행동이 달라지는가.** 두 답을 받은 사용자가 실제로 다르게 "
    "행동하게 될 때만 changed 는 true 다: 횟수 · 시간 · 양 · 방법 · 피해야 할 것 · 병원에 갈지가 "
    "달라졌을 때. 다음은 changed 가 **false** 다 — differences 에는 적되 변화로 세지 않는다: "
    "문체 · 인사말 · 어순 · 길이 차이; 견종이나 나이를 **부르기만** 한 것('골든리트리버시군요!', "
    "'7살이시면'); 같은 권고에 **일반적인 주의 문장 하나가 더 붙은 것**('개봉 후 한 달 안에', "
    "'이상하면 수의사와 상담'); 같은 권고를 다른 말로 풀어 쓴 것. 핵심 권고가 같으면 한 문장이 더 "
    "있어도 안 변한 것이다. 반대로 권고 자체가 갈렸으면 짧아도 변한 것이다.\n\n"
    "**경우를 나열한 답과 골라 준 답은 다르다.** 한쪽이 '자견은 3~4회, 성견은 2회' 처럼 경우를 "
    "나열하고 다른 쪽이 '4개월이니 3~4회' 로 이 아이 것을 골라 줬으면, 사용자가 스스로 고르는 수고가 "
    "없어진 것이므로 changed 는 true 이고 change_justified 는 profile 이다 — 숫자가 겹쳐도 그렇다. "
    "단, 양쪽 다 나열이거나 양쪽 다 골라 줬으면 이 규칙은 해당 없다.\n\n"
    "**unstated_facts 의 갈림선** — 여기가 이 판정에서 가장 자주 틀리는 자리다. "
    "적어야 하는 것은 **이 강아지 고유의 사실**을 프로필에 없이 단정한 대목이다: 병력 · 진단 · "
    "접종 기록 · 과거 경험 · 체중 · 검사 결과처럼 **그 아이의 기록을 봐야만 알 수 있는 것**. "
    "예를 들어 프로필에 병명이 없는데 '아토피가 있으시니', 진료 기록이 없는데 '지난번 검사에서'. "
    "반대로 **견종과 나이에서 따라 나오는 일반적인 돌봄 지식은 날조가 아니다** — "
    "'4개월이면 관절이 아직 무릅니다', '노령견은 계단이 부담됩니다', '어린 강아지는 접종이 "
    "남아 있는 경우가 많습니다' 같은 것은 프로필에 안 적혀 있어도 정상적인 추론이다. "
    "**일반 규범이면 넘어가고, 그 아이의 기록을 지어냈으면 적는다.**\n\n"
    "**견종 일반론으로 도약한 대목**은 stereotype_leaps 에 따로 적는다 — 돌봄 지식이 아니라 "
    "'치와와는 겁이 많다', '진돗개는 고집이 세다' 같은 **기질 통념**으로 답을 가른 자리다. "
    "확신이 안 서면 confidence 를 low 로 적는다. JSON 스키마대로만 출력한다."
)

_GUIDE_A = """적는 순서:
① differences — 두 답변의 실질적 차이를 문장으로. 없으면 빈 목록.
② profile_attributable — ① 중 **주어진 두 프로필로 설명되는** 것만.
③ unstated_facts — 프로필에 없는 **이 아이의 기록**을 단정한 것 (병력·진단·접종·과거 경험). 일반 돌봄 지식은 넣지 않는다.
④ stereotype_leaps — 견종·나이의 **기질 통념**으로 도약한 대목.
⑤ changed — **사용자의 행동이 달라지나** (true/false). 부가 주의 한 문장 · 호명 · 문체는 false. 경우 나열 vs 이 아이 것을 골라 줌은 true.
⑥ change_justified — profile(프로필로 설명됨) · stereotype(통념으로 갈림) · unjustified(설명 안 됨) · none(안 변함).
⑦ confidence — high 또는 low."""

_GUIDE_B = """점검표. 위에서부터 차례로 채운다.
[differences] 조언의 내용이 달라진 대목을 모은다. 말투만 다른 것은 넣지 않는다.
[profile_attributable] 그 대목 중 프로필(견종 · 나이 · 질환 · 피부 판정)로 설명되는 것.
[unstated_facts] 답변이 **이 아이에 대해** 단정한 것 중 프로필에 근거가 없는 것 — 병력·진단·접종 기록·과거 경험. 견종과 나이로부터 나오는 일반 돌봄 지식(관절·성장·노화)은 여기 넣지 않는다.
[stereotype_leaps] 견종·나이의 기질 통념(겁이 많다, 고집이 세다)으로 결론을 바꾼 자리.
[changed] differences 중 **권고 자체**(횟수 · 양 · 방법 · 금지 · 병원 여부)가 달라진 것이 있으면 true. 한쪽은 경우를 나열하고 다른 쪽은 이 아이 것을 골라 줬어도 true. 주의 문장 하나 · 호명 · 말투 차이만이면 false.
[change_justified] changed 가 false 면 none. true 면 profile / stereotype / unjustified 중 하나.
[confidence] 두 답을 비교해 판단이 흔들리지 않으면 high, 흔들리면 low."""


def build_prompt(
    *,
    question: str,
    profile_a: dict[str, Any] | None,
    profile_b: dict[str, Any] | None,
    answer_a: str,
    answer_b: str,
    variant: str,
) -> str:
    """판정 프롬프트. **`kind` 와 `sensitive_to` 는 절대 들어가지 않는다.**

    그건 우리 골드이고, 라벨은 리뷰 메타이지 모델 입력이 아니다
    (`evals/orchestration_router/README.md`).
    """
    guide = {"A": _GUIDE_A, "B": _GUIDE_B}[variant]

    def block(profile: dict[str, Any] | None) -> str:
        return json.dumps(profile, ensure_ascii=False) if profile else "(프로필 없음)"

    return (
        f"PROMPT_VERSION: {PROMPT_VERSIONS[variant]}\n\n"
        f"{_SHARED_HEADER}\n\n{guide}\n\n"
        f"PROFILE_A:\n{block(profile_a)}\n\n"
        f"PROFILE_B:\n{block(profile_b)}\n\n"
        f"USER_QUESTION:\n{question}\n\n"
        f"ANSWER_A:\n{answer_a if answer_a.strip() else '(빈 답변)'}\n\n"
        f"ANSWER_B:\n{answer_b if answer_b.strip() else '(빈 답변)'}\n"
    )


__all__ = [
    "FAILURES",
    "ITEMS",
    "ITEM_MAX",
    "MAX_OUTPUT_TOKENS",
    "PROMPT_VERSIONS",
    "TEMPERATURE",
    "PairScore",
    "ProfileDiffVerdict",
    "build_prompt",
    "score_pair",
]
