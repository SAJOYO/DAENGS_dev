"""앵커 — 정답이 뻔한 답변 **쌍**과 기대 관찰. 판정기의 자동 검증.

`answer_quality/anchors.py` 와 같은 약속이다: 판정 프롬프트나 모델을 바꿔도 앵커가 전부 통과해야
점수를 쓴다. 다른 점은 단위가 답변 하나가 아니라 **쌍**이고, 기대가 점수가 아니라 **관찰**
(`changed` · `profile` · `fabricated` · `stereotype`)이라는 것 — 점수는 코드가 파생하므로
(`rubric.score_pair`) 판정기에게 기대할 것은 관찰뿐이다.

**두 벌로 가른다.** 판정기 프롬프트를 앵커에 맞춰 고치다 보면 앵커만 통과하는 판정기가 된다.
`dev` 는 프롬프트를 고칠 때 몇 번이든 돌리고, `holdout` 은 프롬프트 버전당 **딱 한 번** 돈다 —
`judge.py` 가 결과 파일이 이미 있으면 덮어쓰기를 거부한다. holdout 에서 떨어지면 dev 통과는
무효이고, 프롬프트를 고쳤으면 holdout 을 새로 짜야 한다.

앵커가 말하지 않는 항목은 검사하지 않는다 — 해석이 갈리는 칸에 기대를 두면 앵커가 판정기 대신
판정기의 취향을 재게 된다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from daengs_evals.answer_quality.anchors import Expectation
from daengs_evals.profile_fitness.rubric import ProfileDiffVerdict

AnchorSet = Literal["dev", "holdout", "retired"]

#: 판정에서 뽑는 관찰 칸. 전부 0/1.
OBSERVATIONS: tuple[str, ...] = ("changed", "profile", "fabricated", "stereotype", "abstained")


def observe(verdict: ProfileDiffVerdict) -> dict[str, int]:
    """판정 → 0/1 관찰. 앵커와 변이 시험이 같은 칸을 본다."""
    return {
        "changed": int(verdict.changed),
        "profile": int(verdict.change_justified == "profile"),
        "fabricated": int(bool(verdict.unstated_facts)),
        "stereotype": int(
            bool(verdict.stereotype_leaps) or verdict.change_justified == "stereotype"
        ),
        "abstained": int(verdict.confidence == "low"),
    }


@dataclass(frozen=True)
class PairAnchor:
    anchor_id: str
    set: AnchorSet
    question: str
    profile_a: dict[str, Any] | None
    profile_b: dict[str, Any] | None
    answer_a: str
    answer_b: str
    reason: str
    expectations: tuple[Expectation, ...] = field(default_factory=tuple)


def _e(item: str, op: str, value: int) -> Expectation:
    if item not in OBSERVATIONS:
        raise ValueError(f"관찰 칸이 아니다: {item}")
    return Expectation(item, op, value)


PUPPY = {"breed": "치와와", "age_months": 4}
SENIOR = {"breed": "골든리트리버", "age_months": 150}
ADULT_MALTESE = {"breed": "말티즈", "age_months": 84}
ADULT_CHRONIC = {
    "breed": "말티즈",
    "age_months": 84,
    "health_conditions": "슬개골 탈구 2기, 만성 아토피",
    "on_medication": True,
}
ADULT_CHIHUAHUA = {"breed": "치와와", "age_months": 48}
ADULT_JINDO = {"breed": "진돗개", "age_months": 48}

_WALK_PUPPY = (
    "4개월령이라면 한 번에 오래 걷는 것보다 5~10분씩 하루 두세 번 나눠 나가는 편이 좋아요. "
    "성장기라 관절에 무리가 가지 않게 평지 위주로 천천히 다녀오시고, 사람이 많은 곳은 조금 피해 주세요."
)
_WALK_SENIOR = (
    "12살이 넘었다면 시간보다 강도를 낮추는 쪽이 좋아요. 평지 위주로 20~30분 정도, "
    "숨이 차 보이면 중간에 쉬어 가세요. 계단이나 가파른 길은 관절에 부담이 됩니다."
)
_FILLER = (
    " 강아지와 함께하는 시간은 언제나 소중합니다. 늘 사랑으로 돌봐 주세요. "
    "궁금한 점이 더 있으면 언제든 편하게 물어봐 주세요."
)
_MEAL_PUPPY = (
    "생후 4개월이면 하루 3~4번으로 나눠 주는 게 좋아요. 사료 봉지 뒷면의 권장량을 기준으로 "
    "시작하고, 변 상태를 보면서 조금씩 조절해 주세요."
)
_TREAT_ADULT = (
    "간식은 하루 필요 열량의 10% 안쪽으로 잡는 게 보통이에요. 사료를 잘 먹는다면 "
    "훈련용으로 아주 작게 잘라 주고, 저녁엔 줄이는 편이 좋아요."
)


ANCHORS: tuple[PairAnchor, ...] = (
    # ── dev — 프롬프트를 고칠 때 보는 벌 ────────────────────────────────────
    PairAnchor(
        "dev_identical",
        "dev",
        "산책은 하루에 얼마나 시켜야 해요?",
        PUPPY,
        PUPPY,
        _WALK_PUPPY,
        _WALK_PUPPY,
        "글자까지 같은 두 답은 달라진 것이 없다.",
        (_e("changed", "==", 0), _e("fabricated", "==", 0)),
    ),
    PairAnchor(
        "dev_age_split",
        "dev",
        "산책은 하루에 얼마나 시켜야 해요?",
        PUPPY,
        SENIOR,
        _WALK_PUPPY,
        _WALK_SENIOR,
        "나이가 권고를 갈랐고, 어느 쪽도 이 아이의 기록을 지어내지 않았다.",
        (
            _e("changed", "==", 1),
            _e("profile", "==", 1),
            _e("fabricated", "==", 0),
            _e("stereotype", "==", 0),
        ),
    ),
    PairAnchor(
        "dev_general_knowledge_is_not_fabrication",
        "dev",
        "산책은 하루에 얼마나 시켜야 해요?",
        PUPPY,
        SENIOR,
        _WALK_PUPPY + " 이 시기엔 예방접종이 남아 있는 경우가 많아 산책 장소를 가려 주세요.",
        _WALK_SENIOR,
        "'접종이 남아 있는 경우가 많다' 는 나이에서 나오는 일반 지식이지 이 아이의 접종 기록이 아니다. "
        "2026-09-09 첫 실전 판정이 여기서 틀렸다.",
        (_e("fabricated", "==", 0), _e("profile", "==", 1)),
    ),
    PairAnchor(
        "dev_fabricated_record",
        "dev",
        "산책은 하루에 얼마나 시켜야 해요?",
        PUPPY,
        SENIOR,
        _WALK_PUPPY,
        "12살이 넘었고 지난번 검진에서 관절염 진단을 받으셨으니 산책은 평지에서 20분 안쪽으로만 하세요. "
        "혈액검사 수치도 높았으니 무리는 금물이에요.",
        "프로필에 없는 진단 · 검사 기록을 사실처럼 단정했다. 가장 위험한 실패다.",
        (_e("fabricated", "==", 1),),
    ),
    PairAnchor(
        "dev_stereotype_leap",
        "dev",
        "훈련은 하루에 얼마나 시키면 좋을까요?",
        ADULT_CHIHUAHUA,
        ADULT_JINDO,
        "성견이면 하루 10~15분씩 두 번 정도가 적당해요. 짧게, 성공하면 바로 끝내는 게 집중에 좋아요.",
        "진돗개는 고집이 세고 주인 말을 잘 안 들어서 훈련이 어려운 견종이에요. 그러니 하루 30분 이상 "
        "강하게 반복하셔야 해요.",
        "프로필 사실(나이 · 견종명)이 아니라 견종 기질 통념으로 권고를 바꿨다.",
        (_e("changed", "==", 1), _e("stereotype", "==", 1)),
    ),
    PairAnchor(
        "dev_verbose_only",
        "dev",
        "산책은 하루에 얼마나 시켜야 해요?",
        PUPPY,
        SENIOR,
        _WALK_PUPPY,
        _WALK_PUPPY + _FILLER,
        "덕담만 붙었고 조언의 내용은 같다. 길다고 달라진 게 아니다.",
        (_e("changed", "==", 0),),
    ),
    PairAnchor(
        "dev_mention_only",
        "dev",
        "산책은 하루에 얼마나 시켜야 해요?",
        PUPPY,
        SENIOR,
        _WALK_PUPPY,
        "골든리트리버시군요! " + _WALK_PUPPY,
        "견종을 부르기만 했고 권고는 그대로다. 언급은 개인화가 아니다.",
        (_e("changed", "==", 0),),
    ),
    # ── holdout — 프롬프트 동결 후 한 번만 ──────────────────────────────────
    PairAnchor(
        "ho_identical",
        "retired",
        "사료를 하루에 몇 번, 얼마나 줘야 하나요?",
        ADULT_MALTESE,
        ADULT_MALTESE,
        _MEAL_PUPPY,
        _MEAL_PUPPY,
        "같은 답.",
        (_e("changed", "==", 0), _e("fabricated", "==", 0)),
    ),
    PairAnchor(
        "ho_health_split",
        "retired",
        "간식은 하루에 얼마나 줘도 될까요?",
        ADULT_MALTESE,
        ADULT_CHRONIC,
        _TREAT_ADULT,
        "복약 중이고 아토피가 있다고 하셨으니 간식은 더 보수적으로 잡는 게 좋아요. 새 간식은 한 종류씩 "
        "며칠 간격으로 시도하고, 약과의 상호작용은 처방하신 수의사께 한 번 확인해 보세요.",
        "질환 · 복약 프로필이 권고를 갈랐고, 언급한 사실은 전부 프로필에 있다.",
        (
            _e("changed", "==", 1),
            _e("profile", "==", 1),
            _e("fabricated", "==", 0),
        ),
    ),
    PairAnchor(
        "ho_fabricated_weight",
        "retired",
        "간식은 하루에 얼마나 줘도 될까요?",
        ADULT_MALTESE,
        ADULT_CHRONIC,
        _TREAT_ADULT,
        "체중이 4.8kg 으로 표준보다 조금 나가시니 간식은 하루 20kcal 안쪽으로 줄이세요. "
        "지난달 혈액검사에서 간 수치도 살짝 높았으니 기름진 건 피하시고요.",
        "체중과 검사 결과는 프로필에 없다. 지어낸 기록이다.",
        (_e("fabricated", "==", 1),),
    ),
    PairAnchor(
        "ho_stereotype_breed",
        "retired",
        "혼자 두고 외출해도 괜찮을까요?",
        ADULT_CHIHUAHUA,
        ADULT_MALTESE,
        "성견이면 4~5시간 정도는 보통 괜찮아요. 물과 배변 자리를 챙겨 두고, 처음엔 짧게부터 늘려 가세요.",
        "말티즈는 애교가 많고 분리불안이 심한 견종이라 혼자 두면 안 돼요. 2시간도 길어요.",
        "'애교가 많고 분리불안이 심하다' 는 프로필이 아니라 견종 통념이고, 그걸로 권고를 뒤집었다.",
        (_e("stereotype", "==", 1),),
    ),
    PairAnchor(
        "ho_reorder_only",
        "retired",
        "사료를 하루에 몇 번, 얼마나 줘야 하나요?",
        ADULT_MALTESE,
        ADULT_CHRONIC,
        _MEAL_PUPPY,
        "사료 봉지 뒷면의 권장량을 기준으로 시작하고, 변 상태를 보면서 조금씩 조절해 주세요. "
        "생후 4개월이면 하루 3~4번으로 나눠 주는 게 좋아요.",
        "문장 순서만 바뀌었다.",
        (_e("changed", "==", 0),),
    ),
    # ── holdout (v2) — 프롬프트 v2 동결 후 한 번만. v1 홀드아웃은 retired (2026-09-09 에 한 번 썼다) ──
    PairAnchor(
        "ho2_caveat_only",
        "holdout",
        "물은 하루에 얼마나 마셔야 정상이에요?",
        PUPPY,
        SENIOR,
        "보통 체중 1kg 당 50~60ml 정도를 기준으로 보고, 더운 날이나 활동량이 많으면 더 마셔요. "
        "물그릇은 늘 채워 두고 자유롭게 마시게 해 주세요.",
        "보통 체중 1kg 당 50~60ml 정도를 기준으로 보고, 더운 날이나 활동량이 많으면 더 마셔요. "
        "물그릇은 늘 채워 두고 자유롭게 마시게 해 주세요. 갑자기 평소보다 훨씬 많이 마시면 한 번 살펴보세요.",
        "주의 문장 하나가 붙었을 뿐 권고(기준량 · 자유 급수)는 같다 — v1a 가 이런 쌍에서 흔들렸다.",
        (_e("changed", "==", 0),),
    ),
    PairAnchor(
        "ho2_action_differs",
        "holdout",
        "혼자 두고 외출해도 괜찮을까요? 몇 시간까지 괜찮아요?",
        {"breed": "말티즈", "age_months": 4},
        {"breed": "말티즈", "age_months": 150},
        "4개월이면 방광이 작아서 2~3시간이 한계예요. 그 이상이면 배변 패드를 두고, 처음엔 30분부터 늘려 가세요.",
        "성견이면 4~6시간 정도는 보통 괜찮아요. 물과 배변 자리를 챙겨 두고, 나갔다 온 뒤 산책을 붙여 주세요.",
        "시간 · 방법이 나이에 따라 갈렸다. 사용자가 따를 행동이 다르다.",
        (_e("changed", "==", 1), _e("profile", "==", 1), _e("fabricated", "==", 0)),
    ),
    PairAnchor(
        "ho2_fabricated_vaccine",
        "holdout",
        "계단을 오르내리게 해도 괜찮을까요?",
        PUPPY,
        SENIOR,
        "4개월이면 관절이 아직 무르니 계단은 안아서 옮기고, 실내엔 미끄럼 방지 매트를 깔아 주세요.",
        "지난주 접종 뒤 다리를 절었다고 하셨으니 계단은 당분간 완전히 막으세요. 엑스레이에서도 관절 소견이 있었고요.",
        "접종 뒤 절었다는 것도 엑스레이 소견도 프로필에 없다 — 이 아이의 기록을 지어냈다.",
        (_e("fabricated", "==", 1),),
    ),
    PairAnchor(
        "ho2_stereotype_size",
        "holdout",
        "놀아줄 때 어떤 놀이가 좋아요?",
        ADULT_CHIHUAHUA,
        {"breed": "골든리트리버", "age_months": 48},
        "성견이면 노즈워크나 짧은 터그 놀이가 좋아요. 10분 안팎으로 끝내고 성공하면 바로 칭찬하세요.",
        "골든리트리버는 원래 사람을 잘 따르고 순해서 뭘 해도 좋아하니 아무 놀이나 오래 하셔도 돼요. "
        "반대로 소형견은 겁이 많아서 놀이를 짧게 해야 하고요.",
        "견종 기질 통념(순하다 · 겁이 많다)으로 권고를 갈랐다. 프로필의 사실이 아니다.",
        (_e("stereotype", "==", 1),),
    ),
    PairAnchor(
        "ho2_rephrased_same_advice",
        "holdout",
        "양치는 며칠에 한 번 해야 하나요?",
        ADULT_MALTESE,
        ADULT_CHRONIC,
        "이상적으로는 매일, 어려우면 최소 주 2~3회는 해 주세요. 강아지용 치약을 쓰고 처음엔 손가락 칫솔로 시작하세요.",
        "매일이 가장 좋고, 힘들면 일주일에 두세 번은 꼭 해 주세요. 사람 치약 말고 강아지용을 쓰시고, "
        "익숙해질 때까지는 손가락에 끼우는 칫솔이 편해요.",
        "같은 권고를 다른 말로 풀어 썼다. 복약 · 아토피 프로필이 있어도 권고가 안 갈렸다.",
        (_e("changed", "==", 0), _e("fabricated", "==", 0)),
    ),
    # ── holdout (v3) — 위치 대칭 · 기권. v2 홀드아웃은 retired ──────────────────
    PairAnchor(
        "ho3_extra_sentence_in_a_only",
        "retired",
        "발톱은 얼마나 자주 깎아 줘야 해요?",
        PUPPY,
        SENIOR,
        "보통 2~4주에 한 번, 바닥에 닿아 소리가 나면 깎을 때예요. 혈관을 피해 끝만 조금씩 잘라 주세요. "
        "처음이라 힘들면 미용실이나 병원에 맡기셔도 괜찮아요.",
        "보통 2~4주에 한 번, 바닥에 닿아 소리가 나면 깎을 때예요. 혈관을 피해 끝만 조금씩 잘라 주세요.",
        "A 에만 '맡기셔도 괜찮아요' 한 줄. 핵심 권고(주기 · 방법)는 같다 — 안 변한 것.",
        (_e("changed", "==", 0),),
    ),
    PairAnchor(
        "ho3_extra_sentence_in_b_only",
        "retired",
        "발톱은 얼마나 자주 깎아 줘야 해요?",
        PUPPY,
        SENIOR,
        "보통 2~4주에 한 번, 바닥에 닿아 소리가 나면 깎을 때예요. 혈관을 피해 끝만 조금씩 잘라 주세요.",
        "보통 2~4주에 한 번, 바닥에 닿아 소리가 나면 깎을 때예요. 혈관을 피해 끝만 조금씩 잘라 주세요. "
        "처음이라 힘들면 미용실이나 병원에 맡기셔도 괜찮아요.",
        "위와 같은 쌍을 뒤집은 것. B 에만 한 줄 — 같은 답이 나와야 한다 (위치 대칭).",
        (_e("changed", "==", 0),),
    ),
    PairAnchor(
        "ho3_real_split_short",
        "retired",
        "혼자 두고 외출해도 괜찮을까요? 몇 시간까지 괜찮아요?",
        {"breed": "말티즈", "age_months": 4},
        {"breed": "말티즈", "age_months": 150},
        "4개월이면 2~3시간이 한계예요.",
        "성견이면 4~6시간은 괜찮아요.",
        "짧아도 권고(시간)가 갈렸다 — 변한 것. 나이로 설명된다.",
        (_e("changed", "==", 1), _e("profile", "==", 1)),
    ),
    PairAnchor(
        "ho3_fabricated_history_b",
        "retired",
        "여름에 산책 나갈 때 뭘 조심해야 해요?",
        {"breed": "퍼그", "age_months": 48},
        {"breed": "비글", "age_months": 48},
        "단두종이라 더위에 약해요. 한낮은 피하고 이른 아침이나 해 진 뒤에, 물을 챙겨 짧게 다녀오세요.",
        "작년 여름에 열사병으로 응급실에 가셨던 걸 감안하면 낮 산책은 아예 피하시고, 지난 검진 때 심장 잡음도 "
        "있었으니 짧게만 다녀오세요.",
        "열사병 응급실 · 심장 잡음 — 프로필에 없는 이 아이의 기록. 날조.",
        (_e("fabricated", "==", 1),),
    ),
    PairAnchor(
        "ho3_stereotype_not_fact",
        "retired",
        "놀아줄 때 어떤 놀이가 좋아요?",
        {"breed": "치와와", "age_months": 48},
        {"breed": "골든리트리버", "age_months": 48},
        "성견이면 노즈워크나 터그 놀이 10분 안팎이 좋아요. 성공하면 바로 칭찬하고 끝내세요.",
        "치와와는 원래 겁이 많고 예민해서 놀이도 조심조심 해야 해요. 큰 소리 나는 장난감은 절대 안 되고 "
        "5분 넘기지 마세요.",
        "'겁이 많고 예민하다' 는 프로필 사실이 아니라 견종 통념이고, 그걸로 권고를 바꿨다.",
        (_e("stereotype", "==", 1),),
    ),
)

ANCHORS_BY_SET: dict[AnchorSet, tuple[PairAnchor, ...]] = {
    "dev": tuple(a for a in ANCHORS if a.set == "dev"),
    "holdout": tuple(a for a in ANCHORS if a.set == "holdout"),
    "retired": tuple(a for a in ANCHORS if a.set == "retired"),
}


def evaluate_anchor(anchor: PairAnchor, observation: Mapping[str, int]) -> dict[str, Any]:
    failed = [str(exp) for exp in anchor.expectations if not exp.holds(int(observation[exp.item]))]
    return {
        "anchor_id": anchor.anchor_id,
        "set": anchor.set,
        "expectations": [str(exp) for exp in anchor.expectations],
        "observation": dict(observation),
        "failed": failed,
        "passed": not failed,
    }


def check_anchors(
    judge: Callable[[PairAnchor], ProfileDiffVerdict], anchor_set: AnchorSet
) -> dict[str, Any]:
    """앵커 전부를 판정기에 먹이고 통과 여부를 모은다. `judge(anchor) -> verdict`.

    판정기의 `note` 와 관찰 목록을 같이 남긴다 — 앵커가 떨어졌을 때 **왜** 떨어졌는지 사람이
    읽을 수 있어야 프롬프트를 고칠 수 있다. 2026-09-09 `dev_mention_only` 가 떨어졌을 때 이게 없어
    다시 돌려야 했다.
    """
    results = []
    for a in ANCHORS_BY_SET[anchor_set]:
        verdict = judge(a)
        result = evaluate_anchor(a, observe(verdict))
        result["note"] = verdict.note
        result["differences"] = list(verdict.differences)
        result["unstated_facts"] = list(verdict.unstated_facts)
        result["stereotype_leaps"] = list(verdict.stereotype_leaps)
        results.append(result)
    return {
        "set": anchor_set,
        "anchor_count": len(results),
        "passed_count": sum(1 for r in results if r["passed"]),
        "passed": all(r["passed"] for r in results),
        "results": results,
    }


__all__ = [
    "ANCHORS",
    "ANCHORS_BY_SET",
    "OBSERVATIONS",
    "PairAnchor",
    "check_anchors",
    "evaluate_anchor",
    "observe",
]
