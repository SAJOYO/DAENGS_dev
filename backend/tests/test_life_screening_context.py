"""#283 — 스크리닝 판정이 컨텍스트에서 Life 프롬프트까지 가는 길.

#307 이 `screening_record_id` → 소유권 확인 → `context["screening"]` 까지를 깔았고,
이 파일이 보는 것은 **그 다음 구간**입니다: planner 가 payload 로 옮기는가, 어댑터가
원시값으로 넘기는가, 프롬프트가 그 둘만 싣는가.

두 가지가 이 파일에서 제일 중요합니다.

1. **판정이 없으면 프롬프트가 #283 이전과 한 글자도 같은가.** 골든셋에는 판정 기록이
   없으므로, 여기가 깨지면 이 카드가 Life 지표를 올렸는지 내렸는지 아무도 말할 수 없게
   됩니다 (RAG-028 ⑥ · B4 가 `DOG_BLOCK` 에 세운 것과 같은 성질).
2. **병변 이름이 어느 경로로도 안 새는가.** 2단계 병변명은 holdout 에서 56.6% 틀리고
   (D-023), 지금 그 방어가 서 있는 이유는 **이름을 말하는 코드 경로가 없다**는 사실
   자체입니다. 필드가 하나 늘어도 예외가 안 나므로 테스트가 대신 걸립니다
   (contracts 불변식 15).
"""

import pytest

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    LifePayload,
    ScreeningContext,
)
from daengs_backend.orchestration.planner import assemble_route_plan
from daengs_backend.orchestration.semantic import SemanticRoutingDecision

# ---------------------------------------------------------------- planner


def _life_payload(context: dict) -> LifePayload:
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["life"], handoffs=[]),
        query="피부 치료비 지원 받을 수 있나요?",
        context=context,
        router="llm",
    )
    payload = plan.requests[0].payload
    assert isinstance(payload, LifePayload)
    return payload


def test_신뢰된_context_의_screening_만_payload_로_간다() -> None:
    payload = _life_payload({"screening": {"verdict": "abnormal", "days_ago": 3}})
    assert payload.screening == ScreeningContext(verdict="abnormal", days_ago=3)


def test_판정이_없으면_payload_도_그대로다() -> None:
    assert _life_payload({}).screening is None


def test_병변_이름은_payload_로_못_간다() -> None:
    """계약에 `top1` 이 없는 것이 방어의 전부입니다 (D-023). planner 가 화이트리스트로
    거르므로, 상류가 무엇을 얹어도 두 칸 말고는 통과하지 못합니다.
    """
    payload = _life_payload(
        {
            "screening": {
                "verdict": "abnormal",
                "days_ago": 3,
                "top1": "구진",
                "stage2": {"distribution": [0.6, 0.4]},
                "headline": "이상 소견이 있어요",
                "photo_storage_key": "s3://bucket/key.jpg",
            }
        }
    )
    assert payload.screening == ScreeningContext(verdict="abnormal", days_ago=3)
    assert payload.model_dump()["screening"] == {"verdict": "abnormal", "days_ago": 3}


def test_모양이_틀린_screening_은_요청을_깨지_않는다() -> None:
    """여기서 422 를 내면 기록 하나가 답할 수 있는 질문을 통째로 실패시킵니다 (#307 과 같은 판단)."""
    assert _life_payload({"screening": "abnormal"}).screening is None
    assert _life_payload({"screening": {"verdict": "확실치_않음", "days_ago": 3}}).screening is None
    assert _life_payload({"screening": {"verdict": "abnormal"}}).screening is None
    assert _life_payload({"screening": {"verdict": "abnormal", "days_ago": -1}}).screening is None
    assert _life_payload({"screening": {"verdict": "abnormal", "days_ago": True}}).screening is None


def test_general_폴백은_판정을_안_받는다() -> None:
    """`general` 은 근거 없이 답하는 자리이고(D-057), 자기 거절 코드로 진단을 이미 돌려보냅니다.
    거기에 "이상 소견" 을 쥐여 주면 그 거절이 막으려던 문장을 부르게 됩니다.
    """
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=[], handoffs=[]),
        query="우리 개 피부가 왜 이럴까요?",
        context={"screening": {"verdict": "abnormal", "days_ago": 1}},
        router="llm",
        general_fallback=True,
    )
    general = next(r for r in plan.requests if r.capability == CapabilityName.GENERAL)
    assert not hasattr(general.payload, "screening")


def test_training_은_판정을_안_받는다() -> None:
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["training", "life"], handoffs=[]),
        query="앉아를 어떻게 가르치나요?",
        context={"screening": {"verdict": "normal", "days_ago": 2}},
        router="llm",
    )
    training = next(r for r in plan.requests if r.capability == CapabilityName.TRAINING)
    assert not hasattr(training.payload, "screening")


# ---------------------------------------------------------------- 어댑터


async def _run_life_adapter(payload: LifePayload) -> dict:
    """어댑터를 한 번 돌리고 `daengs_life` 로 넘어간 인자를 돌려준다.

    가짜 `ask` 가 `TimeoutError` 를 내는 것은 의도다 — 이 테스트가 보는 것은 넘어간 값이지
    상류의 답이 아니고, 그 경로가 이미 `test_orchestration_*` 에 있다.
    """
    from daengs_backend.orchestration.adapters.life import LifeCapabilityAdapter

    seen: dict = {}

    def ask(question: str, **kw):
        seen.update({"question": question, **kw})
        raise TimeoutError

    await LifeCapabilityAdapter(ask=ask).run(
        CapabilityRequest(capability=CapabilityName.LIFE, payload=payload),
        request_id="req-1",
    )
    return seen


async def test_어댑터가_판정을_원시값으로_넘긴다() -> None:
    """`ScreeningContext` 를 그대로 넘기면 `daengs_life` 가 오케스트레이션을 의존하게 됩니다."""
    seen = await _run_life_adapter(
        LifePayload(
            question="피부 치료비 지원 받을 수 있나요?",
            screening=ScreeningContext(verdict="abnormal", days_ago=3),
        )
    )
    assert seen["screening_verdict"] == "abnormal"
    assert seen["screening_days_ago"] == 3
    assert not any(isinstance(value, ScreeningContext) for value in seen.values())


async def test_판정이_없으면_어댑터가_None_을_넘긴다() -> None:
    seen = await _run_life_adapter(LifePayload(question="펫보험 가입 나이 제한이 어떻게 되나요?"))
    assert seen["screening_verdict"] is None
    assert seen["screening_days_ago"] is None


# ---------------------------------------------------------------- 프롬프트


def _hit():
    from daengs_life.rag.stages.search import Hit

    return Hit(rank=1, score=0.9, chunk_id="c#1", citation="「동물보호법」 제15조",
               citation_url=None, section=None, document_title="동물보호법",
               content="본문", part=None)


def _base() -> str:
    from daengs_life.rag.stages import generate

    return generate.PROMPT.format(context=generate.build_context([_hit()]), question="질문")


def test_판정이_없으면_프롬프트가_283_이전과_같다() -> None:
    """**이 파일에서 제일 중요한 테스트입니다.** 한 글자라도 달라지면 랩 비교가 깨집니다."""
    from daengs_life.rag.stages import generate

    assert generate.build_prompt("질문", [_hit()]) == _base()
    assert generate.build_prompt("질문", [_hit()], screening=None) == _base()
    assert generate.build_prompt("질문", [_hit()], screening=generate.ScreeningNote()) == _base()
    # 한쪽만 있는 것도 사실이 아니다 — 판정 종류 없이 경과일만으로는 쓸 말이 없다.
    assert (
        generate.build_prompt("질문", [_hit()], screening=generate.ScreeningNote(days_ago=3))
        == _base()
    )


def test_판정이_있으면_블록이_뒤에_붙는다() -> None:
    from daengs_life.rag.stages import generate

    prompt = generate.build_prompt(
        "질문", [_hit()], screening=generate.ScreeningNote(verdict="abnormal", days_ago=3)
    )
    assert prompt.startswith(_base())
    assert "[피부 판정 기록] 3일 전 · 이상 소견 있음" in prompt
    assert "병명이나 증상의 원인을 말하지 마세요" in prompt
    assert "나아졌는지 나빠졌는지 판단하지 마세요" in prompt


def test_반려견_블록과_판정_블록의_순서가_고정이다() -> None:
    """순서를 안 박아 두면 같은 입력이 두 프롬프트가 되고, 그러면 랩 비교의 축이 흔들립니다."""
    from daengs_life.rag.stages import generate

    prompt = generate.build_prompt(
        "질문",
        [_hit()],
        dog=generate.DogProfile(breed="퍼그", age_months=26),
        screening=generate.ScreeningNote(verdict="normal", days_ago=0),
    )
    assert prompt.index("[반려견]") < prompt.index("[피부 판정 기록]")


@pytest.mark.parametrize(
    ("verdict", "days_ago", "expected"),
    [
        ("abnormal", 3, "3일 전 · 이상 소견 있음"),
        ("normal", 12, "12일 전 · 특이 소견 없음"),
        ("retake", 0, "오늘 · 사진으로 판정하지 못함"),
    ],
)
def test_판정을_한국어로_옮긴다(verdict: str, days_ago: int, expected: str) -> None:
    """`retake` 는 "판정 못 함"이지 "이상 없음"이 아닙니다. 영단어를 그대로 두면 모델이
    그렇게 읽을 자리가 남습니다.
    """
    from daengs_life.rag.stages.generate import ScreeningNote

    assert ScreeningNote(verdict, days_ago).describe() == expected


def test_프롬프트에_병변_이름이_들어갈_칸이_없다() -> None:
    """`ScreeningNote` 가 칸 둘인 것이 방어입니다 — 늘리면 이 테스트가 걸립니다 (불변식 15)."""
    import dataclasses

    from daengs_life.rag.stages.generate import ScreeningNote

    assert {f.name for f in dataclasses.fields(ScreeningNote)} == {"verdict", "days_ago"}
