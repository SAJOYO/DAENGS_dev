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
    """`ScreeningNote` 의 칸 목록이 방어입니다 — 늘리면 이 테스트가 걸립니다 (불변식 15).

    `history` 는 #79 3번이 늘린 칸이고 **좁힘을 넓히지 않습니다**: 항목이 `(판정, 경과일)`
    쌍이라 병변 이름이 들어갈 자리가 여전히 없습니다. 늘어난 것은 **건수이지 종류가
    아닙니다.** 종류가 늘어나는 순간(예: `top1`) 이 목록이 달라져 여기서 걸립니다.
    """
    import dataclasses

    from daengs_life.rag.stages.generate import ScreeningNote

    assert {f.name for f in dataclasses.fields(ScreeningNote)} == {
        "verdict",
        "days_ago",
        "history",
    }
    with pytest.raises(TypeError):
        ScreeningNote(verdict="abnormal", days_ago=1, top1="구진·플라크")


# ---------------------------------------------------------------- 이력 (#79 3번)
#
# #307 이 참조 → `context["screening"]` 을, #283 이 그 다음 구간을 깔았습니다. 이력은 같은
# 길의 한 칸 더입니다: planner 화이트리스트 → `LifePayload.screening_history` → 어댑터의
# 원시값 쌍 → 프롬프트의 이전 판정 블록.
#
# 이 절이 지키는 것은 둘입니다. **① 좁힘이 건수와 무관한가** — 항목마다 화이트리스트를
# 다시 지나는지. **② 견주지 말라가 프롬프트에 서 있는가** — 판정 두 개를 나란히 놓으면
# 모델이 추세를 지어낼 자리가 생기고, 그 차이는 강아지의 변화가 아닐 수 있습니다 (D-023).


_HISTORY = [{"verdict": "normal", "days_ago": 30}, {"verdict": "retake", "days_ago": 90}]


def test_신뢰된_context_의_이력만_payload_로_간다() -> None:
    payload = _life_payload({"screening_history": _HISTORY})
    assert payload.screening_history is not None
    assert payload.screening_history.entries == [
        ScreeningContext(verdict="normal", days_ago=30),
        ScreeningContext(verdict="retake", days_ago=90),
    ]


def test_이력이_없으면_payload_도_그대로다() -> None:
    assert _life_payload({}).screening_history is None
    assert _life_payload({"screening_history": []}).screening_history is None


def test_이력_항목의_병변_이름도_payload_로_못_간다() -> None:
    """좁힘은 건수와 무관합니다 — 항목마다 같은 화이트리스트를 지납니다."""
    payload = _life_payload(
        {
            "screening_history": [
                {
                    "verdict": "abnormal",
                    "days_ago": 30,
                    "top1": "구진",
                    "stage2": {"distribution": [0.6, 0.4]},
                    "headline": "이상 소견이 있어요",
                    "photo_storage_key": "s3://bucket/key.jpg",
                }
            ]
        }
    )
    assert payload.model_dump()["screening_history"] == {
        "entries": [{"verdict": "abnormal", "days_ago": 30}]
    }


def test_모양이_틀린_이력_항목은_건너뛴다() -> None:
    """항목 하나가 이상하다고 이력 전체를 버리지 않고, 요청을 깨지도 않습니다 (#307 과 같은 판단)."""
    payload = _life_payload(
        {
            "screening_history": [
                "abnormal",
                {"verdict": "확실치_않음", "days_ago": 3},
                {"verdict": "abnormal"},
                {"verdict": "abnormal", "days_ago": -1},
                {"verdict": "abnormal", "days_ago": True},
                {"verdict": "normal", "days_ago": 30},
            ]
        }
    )
    assert payload.screening_history.entries == [ScreeningContext(verdict="normal", days_ago=30)]
    assert _life_payload({"screening_history": {"verdict": "normal"}}).screening_history is None
    assert _life_payload({"screening_history": "normal"}).screening_history is None


def test_상한을_넘는_이력은_요청을_깨지_않고_잘린다() -> None:
    """상류가 이미 잘라서 보내므로 여기 긴 목록이 오면 상류 결함입니다. 그래도 422 를 내면
    사용자가 보지도 고치지도 못하는 결함 때문에 답할 수 있는 질문이 죽습니다."""
    from daengs_backend.orchestration.contracts import SCREENING_HISTORY_LIMIT

    payload = _life_payload(
        {"screening_history": [{"verdict": "normal", "days_ago": d} for d in range(10)]}
    )
    entries = payload.screening_history.entries
    assert len(entries) == SCREENING_HISTORY_LIMIT
    assert [e.days_ago for e in entries] == list(range(SCREENING_HISTORY_LIMIT))


def test_general_폴백은_이력도_안_받는다() -> None:
    """판정 한 건을 안 주는 이유가 여러 건이라고 달라지지 않습니다 (D-057)."""
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=[], handoffs=[]),
        query="우리 개 피부가 왜 이럴까요?",
        context={"screening_history": _HISTORY},
        router="llm",
        general_fallback=True,
    )
    general = next(r for r in plan.requests if r.capability == CapabilityName.GENERAL)
    assert not hasattr(general.payload, "screening_history")


async def test_어댑터가_이력을_원시값_쌍으로_넘긴다() -> None:
    """`ScreeningHistory` 를 그대로 넘기면 `daengs_life` 가 오케스트레이션을 의존하게 됩니다."""
    from daengs_backend.orchestration.contracts import ScreeningHistory

    seen = await _run_life_adapter(
        LifePayload(
            question="피부 치료비 지원 받을 수 있나요?",
            screening=ScreeningContext(verdict="abnormal", days_ago=3),
            screening_history=ScreeningHistory(
                entries=[
                    ScreeningContext(verdict="normal", days_ago=30),
                    ScreeningContext(verdict="retake", days_ago=90),
                ]
            ),
        )
    )
    assert seen["screening_history"] == (("normal", 30), ("retake", 90))
    assert not any(isinstance(value, ScreeningHistory) for value in seen.values())


async def test_이력이_없으면_어댑터가_빈_튜플을_넘긴다() -> None:
    seen = await _run_life_adapter(LifePayload(question="펫보험 가입 나이 제한이 어떻게 되나요?"))
    assert seen["screening_history"] == ()


def test_이력이_없으면_프롬프트가_283_과_같다() -> None:
    """이번 판정만 있는 요청은 #283 과 **바이트 단위로** 같아야 합니다 — 이력을 얹느라
    그 카드의 지표 비교축을 흔들면 안 됩니다."""
    from daengs_life.rag.stages import generate

    only_now = generate.build_prompt(
        "질문", [_hit()], screening=generate.ScreeningNote(verdict="abnormal", days_ago=3)
    )
    assert (
        generate.build_prompt(
            "질문",
            [_hit()],
            screening=generate.ScreeningNote(verdict="abnormal", days_ago=3, history=()),
        )
        == only_now
    )
    assert "[이전 피부 판정 기록]" not in only_now


def test_이력이_있으면_블록이_하나_더_붙는다() -> None:
    from daengs_life.rag.stages import generate

    prompt = generate.build_prompt(
        "질문",
        [_hit()],
        screening=generate.ScreeningNote(
            verdict="abnormal", days_ago=3, history=(("normal", 30), ("retake", 90))
        ),
    )
    assert "[피부 판정 기록] 3일 전 · 이상 소견 있음" in prompt
    assert (
        "[이전 피부 판정 기록] 30일 전 · 특이 소견 없음, 90일 전 · 사진으로 판정하지 못함"
        in prompt
    )
    assert prompt.index("[피부 판정 기록]") < prompt.index("[이전 피부 판정 기록]")


def test_이번_판정_없이_이력만_있어도_블록이_붙는다() -> None:
    """방금 찍은 판정이 실패한 자리에서 "지난번엔 어땠지" 는 그대로 유효한 질문입니다.
    그때 `SCREENING_BLOCK` 이 안 붙으므로, 병명 금지 문장을 이력 블록도 들고 있어야 합니다."""
    from daengs_life.rag.stages import generate

    prompt = generate.build_prompt(
        "질문", [_hit()], screening=generate.ScreeningNote(history=(("normal", 30),))
    )
    assert "[피부 판정 기록]" not in prompt.replace("[이전 피부 판정 기록]", "")
    assert "[이전 피부 판정 기록] 30일 전 · 특이 소견 없음" in prompt
    assert "이 기록에도 병명은 들어 있지 않습니다" in prompt


def test_이력_블록이_견주는_것을_금지한다() -> None:
    """**이 카드에서 제일 중요한 테스트입니다.** 두 판정의 차이는 강아지의 변화가 아닐 수
    있습니다 — 매번 다른 사진이고, 2단계 병변명은 holdout 에서 56.6% 틀리며 1단계 확률은
    보정 전입니다 (D-023). 계약에 견줄 데이터를 안 둔 것이 방어의 절반이고, 나머지 절반이
    이 문장들입니다."""
    from daengs_life.rag.stages import generate

    prompt = generate.build_prompt(
        "질문", [_hit()], screening=generate.ScreeningNote(history=(("normal", 30),))
    )
    assert "나아졌다·나빠졌다·진행됐다고 말하지 마세요" in prompt
    assert "그때 이런 판정이었다" in prompt
    assert "수의사 진료를 권하세요" in prompt


def test_이력의_어휘가_이번_판정과_같다() -> None:
    """같은 판정이 두 줄에서 다른 말로 나오면 모델이 그것을 다른 판정으로 읽습니다."""
    from daengs_life.rag.stages.generate import ScreeningNote

    for verdict in ("normal", "abnormal", "retake"):
        assert ScreeningNote(history=((verdict, 12),)).describe_history() == ScreeningNote(
            verdict=verdict, days_ago=12
        ).describe()
