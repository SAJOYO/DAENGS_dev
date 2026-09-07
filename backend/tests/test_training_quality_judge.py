"""훈련 RAG judge (D-060). 네트워크는 안 탄다 — 가짜 클라이언트로 계약만 본다.

판정의 **내용**은 여기서 안 잰다. 그건 앵커(`tools.training_quality.anchors`)가 진짜 모델로
재는 것이고, 이 파일이 잡는 것은 그 앞의 배선이다 — 무엇을 채점에서 빼는가, 분모를 어떻게
세는가, 청크를 판정자에게 어떻게 보여주는가.
"""
from __future__ import annotations

from typing import Any

import pytest

from tools.training_quality import anchors as anchors_mod
from tools.training_quality import collect as collect_mod
from tools.training_quality import judge as judge_mod

CHUNKS: list[dict[str, Any]] = [
    {"document_id": "d1", "chunk_index": 0, "heading_path": ["가", "나"], "text": "본문 하나"},
    {"document_id": "d2", "chunk_index": 3, "heading_path": [], "text": "본문 둘"},
]


class _Resp:
    def __init__(self, parsed: Any) -> None:
        self.output_parsed = parsed


class _FakeClient:
    """`responses.parse` 만 흉내 낸다. 부른 요청을 남겨 검사한다."""

    def __init__(self, *verdicts: judge_mod.Verdict) -> None:
        self._queue = list(verdicts)
        self.requests: list[dict[str, Any]] = []
        self.responses = type("R", (), {"parse": self._parse})()

    def _parse(self, **kwargs: Any) -> _Resp:
        self.requests.append(kwargs)
        return _Resp(self._queue.pop(0) if self._queue else None)


def _verdict(grounded: bool, unsupported: list[str] | None = None) -> judge_mod.Verdict:
    return judge_mod.Verdict(
        supported=["뒷받침되는 주장"],
        unsupported=unsupported or [],
        rationale="이유",
        grounded=grounded,
    )


def _row(qid: str, decision: str = "ANSWER", chunks: Any = None) -> dict[str, Any]:
    return {
        "id": qid,
        "question": "질문",
        "answer": "답변",
        "decision": decision,
        "chunks": CHUNKS if chunks is None else chunks,
    }


# ── 프롬프트 조립 ────────────────────────────────────────────────────────


def test_판정자에게_청크_본문을_보여준다() -> None:
    """이 축의 존재 이유다 — 본문을 안 보여주면 '근거 있어 보이는가'라는 다른 질문이 된다."""
    prompt = judge_mod.build_prompt("질문", "답변", CHUNKS)

    assert "본문 하나" in prompt
    assert "본문 둘" in prompt
    assert "[자료 1]" in prompt and "[자료 2]" in prompt


def test_긴_청크는_자르되_잘랐다고_말한다() -> None:
    """말 안 하고 자르면 판정자가 '자료에 없다'와 '잘려서 안 보인다'를 구분할 수 없다."""
    long_chunk = [{"document_id": "d", "text": "가" * (judge_mod.MAX_CHUNK_CHARS + 50)}]

    prompt = judge_mod.build_prompt("질문", "답변", long_chunk)

    assert "잘렸습니다" in prompt
    assert "가" * (judge_mod.MAX_CHUNK_CHARS + 1) not in prompt


def test_정적_지시문이_질문보다_앞에_온다() -> None:
    """프롬프트 캐시의 접두사를 길게 잡으려는 것 — 문항마다 달라지는 부분이 뒤여야 산다."""
    prompt = judge_mod.build_prompt("질문", "답변", CHUNKS)

    assert prompt.index("판정 기준") < prompt.index("[질문]")


# ── 무엇을 채점에서 빼는가 (D-060 ⑤) ────────────────────────────────────


@pytest.mark.parametrize("decision", ["REFUSE", "MEDICAL_REFUSAL", "UNCERTAIN"])
def test_ANSWER_가_아닌_행은_판정하지_않는다(decision: str) -> None:
    """그 문구는 모델이 만든 것이 아니라 service.py 의 상수다. 채점하면 우리 상수를 채점한다."""
    client = _FakeClient(_verdict(True))

    result = judge_mod.judge_rows([_row("a", decision=decision)], cli=client)

    assert result == []
    assert client.requests == []


def test_청크가_없는_행도_판정하지_않는다() -> None:
    """근거 없이 부르면 판정자가 자기 사전 지식으로 채점한다 — 이 모듈이 피하려는 실패다."""
    client = _FakeClient(_verdict(True))

    result = judge_mod.judge_rows([_row("a", chunks=[])], cli=client)

    assert result == []
    assert client.requests == []


def test_ANSWER_행은_판정한다() -> None:
    client = _FakeClient(_verdict(False, ["없는 주장"]))

    result = judge_mod.judge_rows([_row("a")], cli=client)

    assert len(result) == 1
    assert result[0].id == "a"
    assert result[0].grounded is False
    assert result[0].unsupported == ["없는 주장"]


def test_결정성을_위해_temperature_0_를_보낸다() -> None:
    """채점자가 비결정적이면 같은 답변이 랩마다 다른 점수를 받아 랩 대조가 무의미해진다."""
    client = _FakeClient(_verdict(True))

    judge_mod.judge_rows([_row("a")], cli=client, model="m")

    assert client.requests[0]["temperature"] == 0
    assert client.requests[0]["model"] == "m"


def test_스키마의_칸_순서가_판정보다_이유를_먼저_둔다() -> None:
    """판정을 먼저 두면 모델이 답을 정해 놓고 이유를 붙인다 (RAG-007 의 rationale→verdict)."""
    fields = list(judge_mod.Verdict.model_fields)

    assert fields.index("supported") < fields.index("grounded")
    assert fields.index("unsupported") < fields.index("grounded")
    assert fields.index("rationale") < fields.index("grounded")


# ── 일치율의 분모 (RAG-075 ①) ───────────────────────────────────────────


def _judgment(qid: str, grounded: bool) -> judge_mod.Judgment:
    return judge_mod.Judgment(
        id=qid, grounded=grounded, supported=[], unsupported=[], rationale=""
    )


def test_일치율의_분모는_양쪽에_다_있는_문항이다() -> None:
    """한쪽만 본 문항을 일치로 세면 라벨을 안 단 만큼 점수가 올라간다 — 정직한 분모."""
    reference = [_judgment("a", True), _judgment("b", False)]
    candidate = [_judgment("a", True), _judgment("b", False), _judgment("c", True)]

    result = judge_mod.agreement(reference, candidate)

    assert result["n"] == 2       # c 는 기준 쪽에 없으므로 분모에서 빠진다
    assert result["agreed"] == 2


def test_엇갈린_문항을_짚어_준다() -> None:
    result = judge_mod.agreement([_judgment("a", True)], [_judgment("a", False)])

    assert result["n"] == 1 and result["agreed"] == 0
    assert result["mismatch"][0]["id"] == "a"


# ── 사람이 볼 문항 고르기 ────────────────────────────────────────────────


def test_판정과_근거가_어긋난_문항을_고른다() -> None:
    """grounded=False 인데 짚은 주장이 없으면 그 판정은 못 믿는다."""
    bad = judge_mod.Judgment(
        id="x", grounded=False, supported=[], unsupported=[], rationale=""
    )

    picks = judge_mod.disagreements([bad])

    assert len(picks) == 1
    assert "근거로 든 주장이 없다" in picks[0]["why_review"]


def test_참인데_안_되는_주장을_적은_문항도_고른다() -> None:
    contradictory = judge_mod.Judgment(
        id="x", grounded=True, supported=[], unsupported=["뭔가"], rationale=""
    )

    picks = judge_mod.disagreements([contradictory])

    assert len(picks) == 1


def test_모순도_경계도_없으면_고르지_않는다() -> None:
    clean = judge_mod.Judgment(
        id="x", grounded=False, supported=[], unsupported=["하나", "둘"], rationale=""
    )

    assert judge_mod.disagreements([clean]) == []


# ── 앵커 (D-060 ⑥) ──────────────────────────────────────────────────────


def test_앵커는_확인_가능한_사실이어야_한다() -> None:
    """이 칸을 채울 수 없으면 그 앵커는 의견이다 — RAG-075 가 뽑은 뿌리."""
    for anchor in anchors_mod.ANCHORS:
        assert anchor.why_verifiable.strip(), f"{anchor.id} 에 why_verifiable 이 없다"


def test_간식_앵커의_전제가_실제로_성립한다() -> None:
    """앵커의 근거가 '청크에 그 낱말이 없다'이므로, 그것을 코드가 지킨다.

    청크 본문을 고치면 이 테스트가 깨진다 — 그것이 의도다. 앵커의 근거가 사라진 채로
    앵커만 남는 것을 막는다.
    """
    anchor = next(a for a in anchors_mod.ANCHORS if a.id == "ungrounded_treat")
    corpus = " ".join(c["text"] for c in anchor.chunks)

    assert "간식" not in corpus
    assert "간식" in anchor.answer


def test_짝_앵커는_한_구절만_다르다() -> None:
    """한쪽만 두면 '다 거짓이라 하는 judge'나 '다 참이라 하는 judge'가 통과한다."""
    bad = next(a for a in anchors_mod.ANCHORS if a.id == "ungrounded_treat")
    good = next(a for a in anchors_mod.ANCHORS if a.id == "grounded_clean")

    assert bad.question == good.question
    assert bad.chunks == good.chunks
    assert bad.expected_grounded is False and good.expected_grounded is True
    assert "간식" not in good.answer


def test_거짓_기대_앵커는_짚어야_할_낱말을_적어_둔다() -> None:
    """판정만 맞고 엉뚱한 주장을 짚었으면 우연히 맞은 것이라 통과로 세지 않는다."""
    for anchor in anchors_mod.ANCHORS:
        if not anchor.expected_grounded:
            assert anchor.expected_unsupported, f"{anchor.id} 에 expected_unsupported 가 없다"


def test_앵커_검사는_이유가_틀리면_통과시키지_않는다() -> None:
    """판정은 맞는데 엉뚱한 주장을 짚은 judge — 실제로 겪은 실패 모양이다."""
    verdicts = []
    for anchor in anchors_mod.ANCHORS:
        # grounded 는 전부 기대대로, 그러나 짚은 주장은 엉뚱하게
        verdicts.append(_verdict(anchor.expected_grounded, ["엉뚱한 주장"]))
    client = _FakeClient(*verdicts)

    result = anchors_mod.check(cli=client)

    assert result["passed"] is False
    failed = [r for r in result["results"] if not r["passed"]]
    assert failed and all(r["verdict_ok"] for r in failed)  # 판정은 맞았는데 떨어졌다


def test_앵커_검사는_판정과_이유가_다_맞아야_통과한다() -> None:
    client = _FakeClient(
        *[
            _verdict(a.expected_grounded, list(a.expected_unsupported))
            for a in anchors_mod.ANCHORS
        ]
    )

    result = anchors_mod.check(cli=client)

    assert result["passed"] is True
    assert result["n_passed"] == len(anchors_mod.ANCHORS)


# ── 수집기 ──────────────────────────────────────────────────────────────


class _InnerRetriever:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, question: str, top_k: int) -> list[dict[str, Any]]:
        self.calls += 1
        return [
            {
                "chunk_id": "c1",
                "document_id": "d1",
                "chunk_index": 2,
                "text": "청크 본문",
                "metadata": {"heading_path": ["가"]},
                "score": 0.87,
            }
        ]

    def gate(self, question: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
        return {"decision": "PASS"}


def test_수집기는_검색을_한_번만_돌린다() -> None:
    """따로 또 부르면 그 사이 코퍼스가 바뀔 수 있고 임베딩 호출도 두 번이다."""
    inner = _InnerRetriever()
    recorder = collect_mod.RecordingRetriever(inner)

    hits = recorder.search("질문", 4)
    recorder.gate("질문", hits)

    assert inner.calls == 1
    assert recorder.last_hits == hits


def test_수집기가_청크_본문을_붙잡는다() -> None:
    """EvidenceCard 에는 본문이 없다 — 이 우회가 이 카드의 배선이다."""
    recorder = collect_mod.RecordingRetriever(_InnerRetriever())
    recorder.search("질문", 4)

    rows = collect_mod.chunk_rows(recorder.last_hits)

    assert rows[0]["text"] == "청크 본문"
    assert rows[0]["heading_path"] == ["가"]
    assert rows[0]["chunk_id"] == "c1"


def test_수집기는_본문을_자르지_않는다() -> None:
    """자르는 것은 판정기의 몫 — 여기서 자르면 나중에 한도를 올려도 옛 덤프가 못 따라온다."""
    long_text = "가" * (judge_mod.MAX_CHUNK_CHARS * 2)
    rows = collect_mod.chunk_rows([{"chunk_id": "c", "document_id": "d", "text": long_text}])

    assert rows[0]["text"] == long_text


def test_질문_파일의_중복_id_를_거부한다() -> None:
    """id 가 겹치면 판정 파일에서 어느 쪽 판정인지 갈리지 않는다."""
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "q.jsonl"
        path.write_text(
            json.dumps({"id": "a", "question": "하나"}, ensure_ascii=False) + "\n"
            + json.dumps({"id": "a", "question": "둘"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="중복"):
            collect_mod.load_questions(path)
