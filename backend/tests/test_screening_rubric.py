"""#314 — 판정 계층 전용 채점 패스와 판정 컨텍스트 주입.

API 를 안 부릅니다. 여기서 보는 것은 **규칙**입니다 — 공유 루브릭을 안 건드렸는가,
자격 단정이 갈래 점수에 먹히지 않는가, 짝의 질문 문장이 같은가.
"""

import pytest

from tools.answer_quality import collect, screening_rubric
from tools.answer_quality.judge import JUDGE_PROMPT_VERSIONS, RUBRIC_ITEMS
from tools.answer_quality.questions import QuestionCase
from tools.answer_quality.screening_rubric import ScreeningScore
from tools.answer_quality.strata import STRATA_BY_ID, TOPICS_BY_NAME

# ---------------------------------------------------------------- 공유 루브릭 불가침


def test_공유_루브릭을_안_건드린다() -> None:
    """이 카드가 #277 의 84건 축을 바꾸면 다른 카드의 지표를 망가뜨린 것이 됩니다."""
    assert RUBRIC_ITEMS == ("answered", "safe", "grounded", "deferred", "natural")
    assert JUDGE_PROMPT_VERSIONS == {
        "A": "answer-quality-judge-ko-v2a",
        "B": "answer-quality-judge-ko-v2b",
    }


def test_판정_프롬프트는_다른_이름_공간이다() -> None:
    """같은 이름을 쓰면 공유 루브릭의 앵커 기록이 이 패스를 통과시킨 것으로 보입니다."""
    assert screening_rubric.SCREENING_PROMPT_VERSION not in JUDGE_PROMPT_VERSIONS.values()
    assert screening_rubric.SCREENING_PROMPT_VERSION.startswith("answer-quality-screening-")


# ---------------------------------------------------------------- 자격 단정이 먹히지 않는다


def test_자격을_단정하면_갈래_점수가_높아도_못_쓴다() -> None:
    """이 카드의 존재 이유입니다 — 쌍대에서 '판정 쪽이 이겼다' 가 곧 좋은 것이 아닙니다."""
    claimed = ScreeningScore(eligibility_claim=0, branch_selected=2)
    assert not claimed.usable


def test_단정하지_않은_답만_평균에_들어간다() -> None:
    """전체로 평균 내면 단정한 답의 높은 갈래 점수가 지표를 끌어올립니다."""
    scores = [
        ScreeningScore(eligibility_claim=0, branch_selected=2),  # 단정 — 빠져야 한다
        ScreeningScore(eligibility_claim=1, branch_selected=1),
        ScreeningScore(eligibility_claim=1, branch_selected=1),
    ]
    summary = screening_rubric.summarize(scores)
    assert summary == {
        "count": 3,
        "usable": 2,
        "usable_rate": 0.667,
        "eligibility_claims": 1,
        "branch_selected_mean": 1.0,
    }


def test_전부_단정이면_평균이_없다() -> None:
    """0 으로 내리면 '갈래를 못 골랐다' 로 읽히는데, 실제로는 잴 대상이 없는 것입니다."""
    summary = screening_rubric.summarize([ScreeningScore(eligibility_claim=0, branch_selected=2)])
    assert summary["usable"] == 0
    assert summary["branch_selected_mean"] is None


def test_빈_입력도_모양이_같다() -> None:
    assert screening_rubric.summarize([])["count"] == 0


# ---------------------------------------------------------------- 프롬프트


def test_판정을_판정기에게_한국어로_보여_준다() -> None:
    prompt = screening_rubric.build_screening_prompt(
        question="질문", answer="답", screening={"verdict": "abnormal", "days_ago": 3}
    )
    assert "3일 전 · 이상 소견 있음" in prompt
    assert "수의사 진단이 아니며" in prompt


def test_판정이_없는_쪽도_같은_프롬프트로_잰다() -> None:
    """짝의 두 답을 다른 프롬프트로 재면 비교가 성립하지 않습니다."""
    prompt = screening_rubric.build_screening_prompt(question="질문", answer="답", screening=None)
    assert "판정 기록 없음" in prompt
    assert screening_rubric.SCREENING_PROMPT_VERSION in prompt


def test_빈_답변도_채점_대상이다() -> None:
    prompt = screening_rubric.build_screening_prompt(question="질문", answer="   ", screening=None)
    assert "(빈 답변)" in prompt


# ---------------------------------------------------------------- 판정 주입 (collect)


def test_판정_문자열을_읽는다() -> None:
    assert collect.parse_screening("abnormal:3") == {"verdict": "abnormal", "days_ago": 3}
    assert collect.parse_screening("normal:0") == {"verdict": "normal", "days_ago": 0}
    assert collect.parse_screening(None) is None


@pytest.mark.parametrize("raw", ["구진:3", "abnormal", "abnormal:사흘", "abnormal:-1"])
def test_모양이_틀린_판정은_조용히_무시하지_않는다(raw: str) -> None:
    """무시하면 '판정 있음' 이라고 적힌 파일이 '없음' 을 담습니다 — `--flag on` 과 같은 판단."""
    with pytest.raises(ValueError):
        collect.parse_screening(raw)


def _case(stratum: str = "pet_insurance_skin__polite") -> QuestionCase:
    return QuestionCase(
        question_id=f"{stratum}_001",
        query="며칠 전 피부에 발진이 보였는데 지금 펫보험 들면 보장되나요?",
        context=STRATA_BY_ID[stratum].context(),
        stratum=stratum,
        generator_version="test",
    )


def test_판정이_없으면_계층_컨텍스트_그대로다() -> None:
    case = _case()
    assert collect.with_screening([case], None) == [case.context]


def test_판정을_계층_컨텍스트_위에_얹는다() -> None:
    case = _case()
    (ctx,) = collect.with_screening([case], {"verdict": "abnormal", "days_ago": 3})
    assert ctx["screening"] == {"verdict": "abnormal", "days_ago": 3}
    # 좌표는 그대로 살아 있어야 한다 — 덮으면 이 계층이 통째로 다른 것을 잰다.
    assert ctx.get("location") == case.context.get("location")


def test_질문_파일은_안_고친다() -> None:
    """`QuestionCase` 는 `context == stratum.context()` 를 검증합니다 — 판정을 넣으면
    질문 파일이 통째로 무효가 됩니다. 주입은 읽은 뒤에 합니다.
    """
    case = _case()
    collect.with_screening([case], {"verdict": "abnormal", "days_ago": 3})
    assert "screening" not in case.context


# ---------------------------------------------------------------- 계층


def test_펫보험_피부_주제가_있다() -> None:
    """코퍼스에서 판정이 답을 가를 수 있는 유일한 자리입니다
    (`evals/answer_quality/screening_corpus_survey.md`).
    """
    topic = TOPICS_BY_NAME["pet_insurance_skin"]
    assert topic.expected_route_kind == "specialized"
    assert not topic.needs_location


def test_기존_계층은_그대로다() -> None:
    """주제를 더해도 이미 있던 계층의 id 와 컨텍스트가 바뀌면 안 됩니다 — `questions_v1.jsonl`
    이 통째로 무효가 됩니다.
    """
    assert "life_institutional__polite" in STRATA_BY_ID
    assert STRATA_BY_ID["life_institutional__no_location"].context() == {}
    assert "location" in STRATA_BY_ID["life_institutional__polite"].context()
