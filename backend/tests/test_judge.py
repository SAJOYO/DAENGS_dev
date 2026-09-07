"""LLM judge 단위 테스트 — **`data/` 도 API 키도 없이 돈다** (RAG-074 · `D15`).

judge 호출은 가짜 클라이언트로 가른다. 붙잡으려는 것은 판정 그 자체가 아니라 **그 둘레의
규약**이다:

  ① 경계 문항(`expect: abstain`·`refuse`)이 판정에서 빠지는가 — 안 빠지면 **옳게 물러선 답이
    거짓으로 찍히고**, 같은 문항을 `A6` 기대 채점표와 이 표가 반대로 말한다
  ② `disagreements()` 가 두 방향을 다 집는가 — 캘리브레이션 대상을 여기서 고르므로,
    한 방향만 집으면 사람이 라벨링할 표본이 반쪽이 된다
  ③ 판정 파일이 **judge 모델과 프롬프트 버전을 들고 있는가** — 없으면 두 판정 파일의 차이가
    답변 때문인지 judge 때문인지 안 갈린다 (RAG-007 의 "버전 고정")
  ④ 스키마의 **칸 순서**가 `asked` → `rationale` → `answers_question` 인가 —
    그 순서가 곧 생성 순서라 판정을 먼저 두면 모델이 답을 정해 놓고 이유를 붙인다
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from daengs_life.rag.stages import judge, score


# ------------------------------------------------------------------ 가짜 judge
class _FakeResponse:
    def __init__(self, parsed: BaseModel | None) -> None:
        self.output_parsed = parsed


class _FakeResponses:
    def __init__(self, verdicts: dict[str, judge.Verdict], calls: list[dict]) -> None:
        self._verdicts = verdicts
        self.calls = calls

    def parse(self, *, model, input, text_format, temperature):   # SDK 인자 이름 그대로 받는다
        self.calls.append({"model": model, "input": input, "temperature": temperature,
                           "text_format": text_format})
        for needle, verdict in self._verdicts.items():
            if needle in input:
                return _FakeResponse(verdict)
        return _FakeResponse(judge.Verdict(asked="?", rationale="기본", answers_question=True))


class _FakeClient:
    def __init__(self, verdicts: dict[str, judge.Verdict] | None = None) -> None:
        self.calls: list[dict] = []
        self.responses = _FakeResponses(verdicts or {}, self.calls)


def _verdict(ok: bool, asked: str = "물은 것") -> judge.Verdict:
    return judge.Verdict(asked=asked, rationale="이유", answers_question=ok)


def _row(qid: str, question: str, text: str = "답변") -> dict:
    return {"id": qid, "question": question, "text": text, "hits": [], "cited": []}


# ------------------------------------------------------------------ ④ 스키마 칸 순서
def test_verdict_puts_reasoning_before_the_verdict() -> None:
    """**`rationale` → `verdict` 순서**가 RAG-007 의 요구다. 순서가 바뀌면 프롬프트를 고친
    것과 같으므로 `PROMPT_VERSION` 을 올려야 하고, 이 단언이 그 자리를 지킨다."""
    fields = list(judge.Verdict.model_fields)
    assert fields == ["asked", "rationale", "answers_question"]
    assert fields.index("answers_question") == len(fields) - 1


def test_prompt_asks_for_responsiveness_not_truth() -> None:
    """이 축은 **사실 확인이 아니다.** 프롬프트가 그것을 말로 못 박고 있어야 한다 —
    `I4` 는 사실이면서 물은 것의 답이 아니었다."""
    assert "사실인지" in judge.PROMPT
    assert "묻지 않는다" in judge.PROMPT


# ------------------------------------------------------------------ 호출 규약
def test_judge_one_pins_model_and_zero_temperature() -> None:
    """**채점자는 흔들리면 안 된다** — 같은 답변이 랩마다 다른 점수를 받으면 랩 대조가 무의미하다."""
    cli = _FakeClient({"목줄": _verdict(True, "과태료 금액")})
    v = judge.judge_one("목줄 안 하면 얼마?", "20만원입니다", cli=cli, model="fake-judge")

    assert v.answers_question is True
    assert v.asked == "과태료 금액"
    call = cli.calls[0]
    assert call["model"] == "fake-judge"
    assert call["temperature"] == 0
    assert call["text_format"] is judge.Verdict
    # 질문과 답변이 **둘 다** 프롬프트에 들어간다. 하나만 들어가면 이 축은 못 잰다.
    assert "목줄 안 하면 얼마?" in call["input"]
    assert "20만원입니다" in call["input"]


def test_judge_one_raises_when_nothing_parsed() -> None:
    """빈 판정을 참으로 넘기면 **못 잰 문항이 성공으로 세진다.** 조용히 넘어가지 않는다."""

    class _Empty:
        def parse(self, **_kw):
            return _FakeResponse(None)

    cli = _FakeClient()
    cli.responses = _Empty()
    with pytest.raises(RuntimeError, match="판정을 안 냈다"):
        judge.judge_one("q", "a", cli=cli, model="fake-judge")


def test_client_says_which_key_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """키가 없을 때 **생성 키와 헷갈리면 안 된다** — judge 는 일부러 다른 계열이다.

    ⚠ 설정을 비워 놓고 부른다. 실제 `.env` 를 읽게 두면 **키를 넣은 PC 에서만 이 테스트가
    깨진다** — CI 는 통과하는데 사람 PC 에서 빨개지는, 가장 알아채기 나쁜 모양이다.
    """
    from daengs_life.rag.core import config

    monkeypatch.setattr(config.settings, "openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        judge.client()


# ------------------------------------------------------------------ ① 경계 문항
def test_judge_rows_skips_boundary_items() -> None:
    """`expect: abstain`·`refuse` 는 **물러서는 것이 정답**이라 이 축으로 재면 옳은 답이
    거짓으로 찍힌다. 그 축은 `A6`(기대 채점)이 이미 자기 자로 잰다."""
    rows = [_row("Q1", "등록 안 하면?"), _row("B6", "초콜릿 먹었는데 괜찮나요?")]
    ckinds = {"Q1": score.CITABLE, "B6": score.NO_MUST}
    cli = _FakeClient()

    out = judge.judge_rows(rows, cli=cli, ckinds=ckinds)

    assert [j.id for j in out] == ["Q1"]
    # **부르지도 않았다** — 경계 문항에 돈을 쓰지 않는 것까지가 이 규약이다.
    assert len(cli.calls) == 1


def test_judge_rows_scores_everything_without_goldenset() -> None:
    """골든셋을 못 읽으면 표를 막지 않고 전부 채점한다 (`cmd_judge` 가 그렇게 부른다)."""
    rows = [_row("Q1", "등록 안 하면?"), _row("B6", "초콜릿?")]
    out = judge.judge_rows(rows, cli=_FakeClient(), ckinds=None)
    assert [j.id for j in out] == ["Q1", "B6"]


# ------------------------------------------------------------------ ② 엇갈린 문항
def _judgments(**by_id: bool) -> list[judge.Judgment]:
    return [judge.Judgment(id=qid, answers_question=ok, asked="물은 것", rationale="이유")
            for qid, ok in by_id.items()]


def test_disagreements_catches_both_directions() -> None:
    """두 방향이 각각 다른 것을 뜻한다 — 한쪽만 집으면 캘리브레이션 표본이 반쪽이 된다.

      grounded ✓ · judge ✗   근거는 옳은데 **답이 초점을 비껴갔다** (`I4` 의 자리)
      grounded ✗ · judge ✓   답은 됐는데 `must` 를 안 지목했다 (`D4` 가 여는 물음)
    """
    marks = {
        "I4": (True, True),     # (cited, grounded)
        "S4": (False, False),
        "Q1": (True, True),
        "Q2": (False, False),
    }
    js = _judgments(I4=False, S4=True, Q1=True, Q2=False)

    dis = judge.disagreements(marks, js)

    assert [d["id"] for d in dis] == ["I4", "S4"]
    assert dis[0]["grounded"] is True and dis[0]["answers"] is False
    assert dis[1]["grounded"] is False and dis[1]["answers"] is True


def test_disagreements_ignores_items_missing_from_either_side() -> None:
    """양쪽에 다 있는 문항만 본다 — `score.flips` 가 랩 둘을 볼 때와 같은 규약이다."""
    marks = {"Q1": (True, True)}
    js = _judgments(Q1=True, ZZ=False)
    assert judge.disagreements(marks, js) == []


# ------------------------------------------------------------------ 요약 네 칸
def test_summarise_returns_all_four_cells() -> None:
    """**합계 하나로 줄이지 않는다** — `D6`(RAG-071)이 *"총계 한 줄로 성패를 말하면 안 된다"*
    로 잡은 자리를 이 표가 다시 열면 안 된다."""
    marks = {"A": (True, True), "B": (True, True), "C": (False, False), "D": (False, False)}
    js = _judgments(A=True, B=False, C=True, D=False)

    s = judge.summarise(js, marks)

    assert s == {"n": 4, "answers": 2,
                 "both": 1, "grounded_only": 1, "answers_only": 1, "neither": 1}


# ------------------------------------------------------------------ ③ 판정 파일 헤더
def test_header_pins_judge_model_and_prompt_version() -> None:
    """헤더가 이 둘을 안 들면 **두 판정 파일의 차이가 답변 때문인지 judge 때문인지 안 갈린다.**"""
    h = judge.header("lap30", _judgments(Q1=True), model="fake-judge")

    assert h.lap == "lap30"
    assert h.judge_model == "fake-judge"
    assert h.prompt_version == judge.PROMPT_VERSION
    assert h.rubric == judge.RUBRIC == "answers_question"
    assert h.items == 1


def test_default_judge_model_is_not_a_moving_name() -> None:
    """`-latest` 류를 쓰면 판정이 조용히 달라진다 — 옛 판정과의 대조가 통째로 끊긴다."""
    from daengs_life.rag.core import config

    assert "latest" not in config.settings.openai_judge_model
