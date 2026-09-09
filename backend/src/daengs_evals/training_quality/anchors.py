"""앵커 — judge 를 재는 자 (D-060 ⑥).

`score` 를 돌리기 전에 이것이 통과해야 한다. **judge 를 믿을 근거는 이 앵커뿐이다** — 사람
라벨 30개는 채우지 않기로 했으므로(D-060 ⑨) 이 게이트가 임시가 아니라 **영구적인 신뢰
근거**다. 앵커가 깨지면 그 판정 파일은 버린다.

그래서 앵커를 더하는 문턱이 높다. `why_verifiable` 을 채울 수 없는 것은 안 받는다 — 이것이
느슨해지면 judge 를 재는 자가 아예 없어진다.

────────────────────────────────────────────────────────────────────────────
앵커는 **의견이 아니라 확인 가능한 사실**로만 짓는다
────────────────────────────────────────────────────────────────────────────
`RAG-075` 가 뽑은 뿌리가 이 규칙의 이유다. **네 번 반복된** 실패가 하나였다:

    RAG-070 ②   청크를 보고 질의를 짐작했다
    RAG-072 ⑦   순위를 잘못 셌다
    RAG-075 ②   답변 하나를 보고 "이건 답이 아니다" 를 혼자 정했다
                → 사람이 라벨링해 보니 **judge 가 맞고 그 읽기가 틀렸다**
    D-060 첫 랩  판정 둘을 보고 "이건 표현 트집이다" 를 혼자 정했다
                → 사람이 6건을 보니 **judge 가 6/6 맞았다** (2026-09-07)

넷 다 *"재는 자 없이 판단했다"* 다. ⚠ **네 번째는 이 모듈을 쓴 그 세션에서 났다** — 위 세
줄을 여기 적어 놓고 같은 자리에 빠졌다. 경고를 적는 것으로는 안 막힌다는 뜻이다.

**"이 답변은 좋다/나쁘다" 로 앵커를 지으면 그 실패를 앵커에 그대로 넣는 것이다.** 그래서 여기
있는 것은 전부 이 모양이다:

    이 문자열이 청크 넷에 있는가 / 없는가 — 누구든 열어서 확인한다

`expected_unsupported` 가 그 장치다. 단순히 `grounded=False` 를 기대하는 것이 아니라
**judge 가 어느 주장을 짚어야 하는지**까지 적는다. 판정은 맞는데 엉뚱한 주장을 짚었다면
그 judge 는 우연히 맞은 것이고, 앵커는 그것을 통과로 세면 안 된다.

────────────────────────────────────────────────────────────────────────────
왜 짝으로 두는가
────────────────────────────────────────────────────────────────────────────
`ungrounded_treat` 와 `grounded_clean` 은 **질문·자료가 같고 답변만 한 구절 다르다.**
한쪽만 두면 "다 거짓이라 하는 judge" 나 "다 참이라 하는 judge" 가 통과한다 — 실제로
`gpt-4o-mini` 는 후자였다 (`judge_model_probe_0907.md`). 짝이라야 그것이 걸린다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 두 앵커가 공유하는 자료. **"간식"·"5분" 같은 낱말이 여기 없다는 것이 앵커의 전부**라,
#: 이 본문을 고치면 앵커의 근거가 사라진다. 고칠 일이 있으면 기대값도 같이 다시 짜라.
_WALK_CHUNKS: list[dict[str, Any]] = [
    {
        "document_id": "dog_walk_basics",
        "chunk_index": 3,
        "heading_path": ["산책", "당김 행동"],
        "text": (
            "당김 행동은 대개 의도적 반항이 아니라 학습된 결과다. 개가 당길 때 보호자가 "
            "따라가면 '당기면 앞으로 간다'가 성립해 행동이 강화된다."
        ),
    },
    {
        "document_id": "dog_walk_basics",
        "chunk_index": 4,
        "heading_path": ["산책", "느슨한 줄 걷기"],
        "text": (
            "느슨한 줄 걷기 훈련은 멈춤-보상 구조로 만든다. 줄이 팽팽해지면 즉시 멈추고, "
            "개가 돌아보거나 줄이 느슨해지면 다시 출발한다. 1회 5~10분의 짧은 세션을 반복한다."
        ),
    },
    {
        "document_id": "equipment_safety",
        "chunk_index": 1,
        "heading_path": ["장비", "목줄과 하네스"],
        "text": (
            "초크체인·프롱칼라는 목 부위에 압박을 주어 기도와 경추에 손상을 줄 수 있다. "
            "하네스나 일반 목줄을 권장한다."
        ),
    },
    {
        "document_id": "dog_walk_basics",
        "chunk_index": 9,
        "heading_path": ["산책", "출발 전"],
        "text": (
            "산책 전 짧은 집중 훈련은 흥분도를 낮춘다. 현관 앞에서 앉아 기다리기를 시킨 뒤 "
            "문을 연다."
        ),
    },
]

_WALK_QUESTION = "강아지가 산책 중에 자꾸 목줄을 당기는데 어떻게 교정하나요?"


@dataclass(frozen=True)
class Anchor:
    """코드가 지은 문항 하나와 기대 판정.

    `why_verifiable` 은 장식이 아니다 — **앵커를 더할 때 이 칸을 채울 수 없으면 그 앵커는
    의견이다.** 채울 수 있는 것만 들어온다.
    """

    id: str
    question: str
    answer: str
    chunks: list[dict[str, Any]]
    expected_grounded: bool
    why_verifiable: str
    #: judge 의 `unsupported` 에 **반드시 들어 있어야 할 낱말**. 판정만 맞고 엉뚱한 주장을
    #: 짚었으면 우연히 맞은 것이라 통과로 세지 않는다. `expected_grounded=True` 면 빈다.
    expected_unsupported: tuple[str, ...] = field(default=())


ANCHORS: tuple[Anchor, ...] = (
    Anchor(
        id="ungrounded_treat",
        question=_WALK_QUESTION,
        answer=(
            "목줄 당김은 당기는 행동이 '앞으로 나아간다'는 보상으로 이어질 때 강화됩니다 [1]. "
            "가장 먼저 할 일은 당길 때 멈추고, 줄이 느슨해지면 다시 걷는 것입니다 [1][2]. "
            "하루 5~10분씩 짧게 반복하고, 느슨한 상태에서 간식을 주어 위치를 알려 주세요 [2]. "
            "목이 조이는 초크체인은 기도와 경추를 압박할 수 있어 권하지 않습니다 [3]."
        ),
        chunks=_WALK_CHUNKS,
        expected_grounded=False,
        why_verifiable=(
            "'간식'이 네 청크 어디에도 없다. 문자열 부재라 누구든 위 _WALK_CHUNKS 를 열어 "
            "확인한다 — 답변이 좋은지에 대한 의견이 아니다. 나머지 주장(멈춤-보상 · 5~10분 · "
            "초크체인 위험)은 전부 자료에 있어서, 걸리는 것이 이 한 구절뿐이라는 것도 확인된다."
        ),
        expected_unsupported=("간식",),
    ),
    Anchor(
        id="grounded_clean",
        question=_WALK_QUESTION,
        # `ungrounded_treat` 에서 간식 구절 **하나만** 뺐다. 다른 곳은 글자까지 같다.
        answer=(
            "목줄 당김은 당기는 행동이 '앞으로 나아간다'는 보상으로 이어질 때 강화됩니다 [1]. "
            "가장 먼저 할 일은 당길 때 멈추고, 줄이 느슨해지면 다시 걷는 것입니다 [1][2]. "
            "하루 5~10분씩 짧게 반복해 주세요 [2]. "
            "목이 조이는 초크체인은 기도와 경추를 압박할 수 있어 권하지 않습니다 [3]."
        ),
        chunks=_WALK_CHUNKS,
        expected_grounded=True,
        why_verifiable=(
            "위 앵커에서 간식 구절만 뺀 것이라 남은 주장이 전부 자료에 있다 — 멈춤-보상은 "
            "자료 2, 5~10분도 자료 2, 초크체인은 자료 3. 짝으로 두는 이유는 '다 거짓이라 하는 "
            "judge'를 잡기 위해서다."
        ),
    ),
    Anchor(
        id="contradicts_source",
        question=_WALK_QUESTION,
        answer=(
            "줄이 팽팽해지면 멈추고 느슨해지면 다시 출발하세요 [2]. "
            "당김이 심한 경우에는 초크체인을 써서 당길 때마다 목에 압박을 주면 빠르게 "
            "교정됩니다 [3]."
        ),
        chunks=_WALK_CHUNKS,
        expected_grounded=False,
        why_verifiable=(
            "자료 3 은 초크체인을 '손상을 줄 수 있다'며 **권하지 않는다**. 답변은 그것을 "
            "권한다 — 자료에 없는 것이 아니라 자료와 **반대**다. 부재와 모순은 다른 실패라 "
            "따로 잡는다. 자료 3 의 본문과 답변 문장을 나란히 놓으면 확인된다."
        ),
        expected_unsupported=("초크체인",),
    ),
)


def check(*, cli: Any = None, model: str | None = None) -> dict[str, Any]:
    """앵커를 전부 돌려 judge 가 쓸 만한지 본다. **`score` 앞에 이것이 통과해야 한다.**

    통과 조건이 둘이다:

      ① `grounded` 가 기대와 같다
      ② `expected_unsupported` 의 낱말이 judge 의 `unsupported` 목록에 실제로 있다

    ②가 있는 이유는 **판정만 맞고 이유가 틀린 judge 를 통과시키지 않으려는 것**이다.
    `grounded=False` 는 아무 주장이나 짚어도 나올 수 있고, 그러면 다음 랩에서 다른 답변을
    같은 이유로 틀리게 잡는다.
    """
    from daengs_evals.training_quality import judge as judge_mod

    results = []
    for anchor in ANCHORS:
        verdict = judge_mod.judge_one(
            anchor.question, anchor.answer, anchor.chunks, cli=cli, model=model
        )
        verdict_ok = verdict.grounded == anchor.expected_grounded
        blob = " ".join(verdict.unsupported)
        missing = [word for word in anchor.expected_unsupported if word not in blob]
        results.append(
            {
                "id": anchor.id,
                "passed": verdict_ok and not missing,
                "expected_grounded": anchor.expected_grounded,
                "actual_grounded": verdict.grounded,
                "verdict_ok": verdict_ok,
                "missing_reasons": missing,
                "unsupported": verdict.unsupported,
                "rationale": verdict.rationale,
            }
        )
    passed = sum(1 for r in results if r["passed"])
    return {
        "passed": passed == len(ANCHORS),
        "n": len(ANCHORS),
        "n_passed": passed,
        "prompt_version": judge_mod.PROMPT_VERSION,
        "results": results,
    }
