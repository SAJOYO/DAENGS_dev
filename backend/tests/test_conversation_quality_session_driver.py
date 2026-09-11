"""`SessionDriver` — 이력 기제를 실제로 태우는 드라이버 (#416, task 9).

가짜 오케스트레이터만 쓴다 — 진짜 `AssistantOrchestrationService` 를 조립하면 backend
설정(`GEMINI_API_KEY` 등)이 필요해진다(`build_session_driver` 의 늦은 import 가 그것을
피하는 이유). 여기서는 `send()` 계약 — 무엇을 오케스트레이터에 보내는지, payload 로
무엇을 돌려주는지 — 만 잰다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from daengs_evals.conversation_quality.drivers import SessionDriver, StatelessDriver
from daengs_evals.conversation_quality.transcript import PRIOR_TURNS_REACH_INFERENCE


class _RecordingOrchestrator:
    """매 호출마다 실제로 받은 `prior_turns` 의 길이를 기록하고 항상 답한다."""

    def __init__(self) -> None:
        self.seen_prior_turns_lengths: list[int] = []

    async def run(
        self, *, query, principal, context, prior_turns=(), pending_clarification=None
    ):
        self.seen_prior_turns_lengths.append(len(prior_turns))
        return SimpleNamespace(
            status=SimpleNamespace(value="ANSWERED"),
            message=f"답: {query}",
            results=[],
            clarify=None,
        )


class _ClarifyingOrchestrator:
    """첫 호출은 `CLARIFY` 로 되묻고, 둘째 호출부터는 `ANSWERED` 로 답한다.

    `_RecordingOrchestrator` 와 같은 이유로 `seen_pending_clarifications` 를 남긴다 —
    드라이버의 **내부 상태**(`last_pending_question`)만 보면 "계산은 맞게 하고 오케스트레이터
    에는 안 보낸다" 는 결함을 못 잡는다(R26). `test_session_driver_sends_full_history_…`
    가 `prior_turns` 에 대해 하는 것과 같은 확인을, `pending_clarification` 에 대해서도 한다.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.seen_pending_clarifications: list[Any] = []

    async def run(
        self, *, query, principal, context, prior_turns=(), pending_clarification=None
    ):
        self.calls += 1
        self.seen_pending_clarifications.append(pending_clarification)
        if self.calls == 1:
            return SimpleNamespace(
                status=SimpleNamespace(value="CLARIFY"),
                message="평소와 비교해 식욕에 달라진 점이 있나요?",
                results=[],
                clarify=SimpleNamespace(
                    question="평소와 비교해 식욕에 달라진 점이 있나요?",
                    missing=["observation"],
                    missing_axes=["APPETITE"],
                ),
            )
        return SimpleNamespace(
            status=SimpleNamespace(value="ANSWERED"),
            message="확인했습니다.",
            results=[],
            clarify=None,
        )


def test_the_constant_is_flipped() -> None:
    """이 상수 하나가 카드와 하네스를 잇는 자리다 (#401 §3).

    무너뜨리는 한 줄: `transcript.PRIOR_TURNS_REACH_INFERENCE = False` 로 되돌리면 실패한다.
    """
    assert PRIOR_TURNS_REACH_INFERENCE is True


def test_session_driver_accumulates_turns_and_reports_indices_not_text() -> None:
    """랩 파일에 본문을 안 적는다 — 본문은 이미 `cases_v1.jsonl` 에 있다 (스펙 ④).

    무너뜨리는 한 줄: `send()` 의 `"prior_turns_supplied": list(range(len(prior_turns)))` 를
    `"prior_turns_supplied": []` 로 하드코딩하면(StatelessDriver 를 베낀 실수) 실패한다.
    """
    driver = SessionDriver(_RecordingOrchestrator(), principal=None, adapter_mode="fake")
    first = driver.send("심장사상충 예방약 먹여야 해?")
    assert first["prior_turns_supplied"] == []
    payload = driver.send("그거 얼마나 자주 해?")
    assert payload["prior_turns_supplied"] == [0]
    assert all(isinstance(i, int) for i in payload["prior_turns_supplied"])
    assert "심장사상충" not in repr(payload["prior_turns_supplied"])
    # payload 전체를 뒤져도 원문이 없다 — `prior_turns_supplied` 칸만 우연히 비어 텍스트가
    # 안 보이는 것이 아니라, 드라이버가 애초에 어느 칸에도 원문을 담지 않는다는 것을 잰다.
    assert "심장사상충" not in repr(payload)


def test_session_driver_sends_full_history_to_orchestrator_but_not_payload() -> None:
    """드라이버가 실제로 **누적해서** 보내는지는 한 번만 보내는 테스트로는 못 잡는다 —
    세 번 보내 오케스트레이터가 본 길이가 0, 1, 2 로 느는지까지 확인한다.

    무너뜨리는 한 줄: `send()` 에서 `prior_turns=prior_turns` 대신 `prior_turns=()` 를
    오케스트레이터에 넘기면(누적은 하되 안 보내는 실수) 이 테스트가 잡고,
    위 테스트는 못 잡는다(오케스트레이터가 무엇을 받았는지 안 보므로) — 그래서 둘 다 있다.
    """
    orchestrator = _RecordingOrchestrator()
    driver = SessionDriver(orchestrator, principal=None, adapter_mode="fake")
    driver.send("심장사상충 예방약 먹여야 해?")
    driver.send("그거 얼마나 자주 해?")
    driver.send("세 번째 질문")
    assert orchestrator.seen_prior_turns_lengths == [0, 1, 2]


def test_session_driver_carries_a_pending_clarification_forward() -> None:
    """무너뜨리는 한 줄: `CLARIFY` 분기에서 `self._pending = ...` 대입을 지우면(대기를
    안 이어가면) `last_pending_question` 이 `None` 으로 남아 실패한다.
    """
    driver = SessionDriver(_ClarifyingOrchestrator(), principal=None, adapter_mode="fake")
    first = driver.send("오늘 건강 상태는 어때?")
    assert first["status"] == "CLARIFY"
    assert driver.last_pending_question is not None
    driver.send("밥은 먹는데 계속 누워 있어")
    # 답을 받았으니 대기가 비워진다 — 이 assert 가 없으면 "대기를 영영 안 지운다"는
    # 반대쪽 결함(응답 하나가 그다음 모든 턴에 되묻기로 계속 묶이는 것)을 못 잡는다.
    assert driver.last_pending_question is None


def test_session_driver_actually_sends_the_pending_clarification_to_the_orchestrator() -> None:
    """드라이버의 **내부 상태**(`last_pending_question`)만 보면 계산은 맞게 하고 정작
    오케스트레이터에는 `pending_clarification=None` 을 하드코딩해서 보내도 통과한다(R26).
    여기서는 오케스트레이터가 **실제로 받은 값**을 잰다.

    무너뜨리는 한 줄: `send()` 의 `pending_clarification=pending_clarification` 을
    `pending_clarification=None` 으로 하드코딩하면, 위 테스트는 여전히 통과하지만
    (드라이버 내부 상태는 정상 계산되므로) 이 테스트가 잡는다.
    """
    orchestrator = _ClarifyingOrchestrator()
    driver = SessionDriver(orchestrator, principal=None, adapter_mode="fake")
    driver.send("오늘 건강 상태는 어때?")
    # 첫 호출 전에는 대기가 없었다 — 정직하게 `None` 을 보냈어야 한다.
    assert orchestrator.seen_pending_clarifications[0] is None
    driver.send("밥은 먹는데 계속 누워 있어")
    # 둘째 호출에는 첫 응답의 되묻기가 실제로 실려야 한다 — 드라이버가 계산만 하고
    # 오케스트레이터에는 안 보내는 결함을 여기서 잡는다.
    forwarded = orchestrator.seen_pending_clarifications[1]
    assert forwarded is not None
    assert forwarded.question == "평소와 비교해 식욕에 달라진 점이 있나요?"
    assert list(forwarded.missing_axes) == ["APPETITE"]


def test_session_driver_payload_keys_match_stateless_driver() -> None:
    """`#415` 의 `SessionDriver` 는 `StatelessDriver` 와 같은 칸을 쓴다는 약속(모듈
    docstring) — 리포트·비교 코드가 드라이버 종류를 몰라도 되게 하려는 것이다.

    무너뜨리는 한 줄: `SessionDriver.send` 의 반환 dict 에 칸 하나(예: 이력 관련 새
    칸)를 더 넣으면, `StatelessDriver` 는 그 칸이 없으므로 `set` 비교가 실패한다.
    """
    session = SessionDriver(_RecordingOrchestrator(), principal=None, adapter_mode="fake")
    stateless = StatelessDriver(_RecordingOrchestrator(), principal=None, adapter_mode="fake")
    session_payload = session.send("질문")
    stateless_payload = stateless.send("질문")
    assert set(session_payload) == set(stateless_payload)


def test_stateless_driver_still_honestly_reports_no_prior_turns() -> None:
    """`StatelessDriver` 는 이 카드로 안 건드렸다 — 여전히 이전 턴을 안 보내고, 정직하게
    빈 리스트를 말해야 한다 (`SessionDriver` 가 상속하거나 이 클래스를 바꾸면 안 된다).

    무너뜨리는 한 줄: `StatelessDriver` 가 `SessionDriver` 를 상속하거나 이력을 쌓기
    시작하면 `seen_prior_turns_lengths` 가 `[0, 1]` 이 되어 실패한다.
    """
    orchestrator = _RecordingOrchestrator()
    driver = StatelessDriver(orchestrator, principal=None, adapter_mode="fake")
    driver.send("첫 질문")
    payload = driver.send("둘째 질문")
    assert payload["prior_turns_supplied"] == []
    assert orchestrator.seen_prior_turns_lengths == [0, 0]
