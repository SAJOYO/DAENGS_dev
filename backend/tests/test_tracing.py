"""LangSmith 트레이싱 — 무엇이 나가고 무엇이 안 나가는가.

이 파일이 지키는 것은 셋이다.

1. **기본은 꺼져 있다.** 환경 변수가 없으면 클라이언트를 만들지도 않는다. 이것이
   D-037 의 "기본 금지" 를 코드가 지키는 자리다.
2. **켰을 때 신원과 정밀 좌표는 지워지고, 질문·답변·청크는 남는다.** 둘 다
   단언한다 — 지우는 쪽만 보면 "전부 지우기" 가 통과해 버리는데, 그건 트레이싱을
   켤 이유 자체를 없애는 회귀다.
3. **`run_id` 가 `request_id` 다.** 신고 한 건에서 트레이스로 가는 길이 그 등식
   하나에 걸려 있다 (`answer_reports.turn_id` → `chat_turns.request_id`).

마스킹 함수를 `OrchestratorState` 와 **같은 모양**의 payload 로 검사하는 것이
핵심이다. 필드 이름만 따로 적어 두고 통과시키면, 상태가 한 겹 깊어지는 날
조용히 새기 시작한다.
"""

from __future__ import annotations

import uuid

import pytest

from daengs_backend.core.tracing import (
    COORDINATE_PRECISION,
    REDACTED,
    configure_tracing,
    scrub_payload,
    trace_config,
    tracing_enabled,
)

#: 실제 그래프가 노드 입력으로 보내는 것과 같은 모양. `graph.py` 의 `initial` 참고.
STATE_SHAPED = {
    "request_id": "0e4d4f6c-1f3c-4a1b-9d1e-2c3b4a5d6e7f",
    "principal": {"subject": "8f14e45f-ceea-467a-9f0e-2b8e1a0d3c44", "kind": "APP_USER"},
    "query": "우리 개가 산책 중에 짖어요. 어떻게 교육하죠?",
    "locale": "ko-KR",
    "context": {
        "location": {"lat": 37.566826, "lon": 126.978656},
        "dog": {"breed": "퍼그", "age_months": 18},
    },
    "route_plan": {
        "requests": [
            {"capability": "training", "payload": {"question": "산책 중에 짖어요"}},
            {"capability": "place", "payload": {"query": "근처 공원", "lat": 37.566826,
                                                "lon": 126.978656}},
        ],
        "router": "llm",
    },
}


class TestScrub:
    def test_신원은_지운다(self) -> None:
        scrubbed = scrub_payload(STATE_SHAPED)
        assert scrubbed["principal"]["subject"] == REDACTED
        # 종류는 남는다 — 사람을 가리키지 않고, 관리자/앱회원을 가르는 데 쓴다.
        assert scrubbed["principal"]["kind"] == "APP_USER"

    def test_좌표는_반올림한다(self) -> None:
        scrubbed = scrub_payload(STATE_SHAPED)
        location = scrubbed["context"]["location"]
        assert location["lat"] == round(37.566826, COORDINATE_PRECISION)
        assert location["lon"] == round(126.978656, COORDINATE_PRECISION)
        # 지우지 않는다 — "좌표를 받기는 했나" 가 진단 대상이다.
        assert isinstance(location["lat"], float)

    def test_중첩된_payload_안의_좌표도_잡는다(self) -> None:
        """`context.location` 만 보고 있으면 `route_plan` 안의 것이 그대로 나간다."""
        place = scrub_payload(STATE_SHAPED)["route_plan"]["requests"][1]["payload"]
        assert place["lat"] == round(37.566826, COORDINATE_PRECISION)
        assert place["lon"] == round(126.978656, COORDINATE_PRECISION)

    def test_질문과_반려견_맥락은_남는다(self) -> None:
        """**전부 지우면 통과하는 테스트가 되지 않게 하는 자리다.**

        질문 원문이 안 나가면 트레이스를 켤 이유가 없다. 견종·월령은 답이 왜
        그렇게 나왔는지의 입력이라 같이 남는다 — 사람을 가리키지 않는다.
        """
        scrubbed = scrub_payload(STATE_SHAPED)
        assert scrubbed["query"] == STATE_SHAPED["query"]
        assert scrubbed["context"]["dog"] == {"breed": "퍼그", "age_months": 18}
        assert scrubbed["route_plan"]["requests"][0]["payload"]["question"] == "산책 중에 짖어요"

    def test_원본을_고치지_않는다(self) -> None:
        """마스킹은 나가는 사본에만 건다. 상태를 고치면 응답이 바뀐다."""
        scrub_payload(STATE_SHAPED)
        assert STATE_SHAPED["principal"]["subject"] == "8f14e45f-ceea-467a-9f0e-2b8e1a0d3c44"
        assert STATE_SHAPED["context"]["location"]["lat"] == 37.566826

    def test_bool_은_좌표가_아니다(self) -> None:
        """파이썬에서 bool 은 int 다. 반올림하면 True 가 1.0 이 된다."""
        assert scrub_payload({"lat": True}) == {"lat": True}

    @pytest.mark.parametrize("value", [None, 3, "문자열", [], {}])
    def test_스칼라는_그대로(self, value: object) -> None:
        assert scrub_payload(value) == value


class TestTraceConfig:
    def test_run_id_가_request_id_다(self) -> None:
        """신고 → 트레이스 링크가 이 등식 하나에 걸려 있다."""
        request_id = str(uuid.uuid4())
        config = trace_config(request_id=request_id, run_name="assistant_query")
        assert config["run_id"] == uuid.UUID(request_id)
        assert config["metadata"]["request_id"] == request_id

    def test_UUID_가_아니면_run_id_없이_나머지만(self) -> None:
        """호출자가 X-Request-ID 를 그대로 넘길 수 있다. 링크만 못 걸 뿐 요청은 산다."""
        config = trace_config(request_id="req-not-a-uuid", run_name="assistant_query")
        assert "run_id" not in config
        assert config["metadata"]["request_id"] == "req-not-a-uuid"

    def test_메타데이터와_태그가_실린다(self) -> None:
        config = trace_config(
            request_id=str(uuid.uuid4()),
            run_name="assistant_query",
            metadata={"router": "llm"},
            tags=["cap:training"],
        )
        assert config["run_name"] == "assistant_query"
        assert config["metadata"]["router"] == "llm"
        assert config["tags"] == ["cap:training"]


class TestGraphWiring:
    """`OrchestrationEngine.run` 이 실제로 그 config 를 `ainvoke` 에 넘기는가.

    `trace_config` 단위 테스트만으로는 **아무도 그것을 부르지 않는 회귀**를 못 잡는다.
    여기서 보는 것은 함수의 반환값이 아니라 그것이 그래프에 닿는가다.
    """

    @staticmethod
    def _engine_capturing_config(captured: dict) -> object:
        from daengs_backend.orchestration.contracts import (
            CapabilityName,
            CapabilityResult,
            CapabilityStatus,
        )
        from daengs_backend.orchestration.graph import OrchestrationEngine

        class FakeTrainingAdapter:
            capability = CapabilityName.TRAINING

            async def run(self, request, *, request_id: str) -> CapabilityResult:
                return CapabilityResult(
                    capability=self.capability,
                    status=CapabilityStatus.OK,
                    data={"answer": "짖음 교육 방법입니다"},
                    elapsed_ms=1,
                )

        engine = OrchestrationEngine({CapabilityName.TRAINING: FakeTrainingAdapter()})
        real_ainvoke = engine.graph.ainvoke

        async def capturing(state, config=None, **kwargs):
            captured.update(config or {})
            return await real_ainvoke(state, config, **kwargs)

        engine.graph.ainvoke = capturing  # type: ignore[method-assign]
        return engine

    @pytest.mark.asyncio
    async def test_그래프가_request_id_를_run_id_로_받는다(self) -> None:
        from daengs_backend.orchestration.contracts import PrincipalContext, RoutePlan

        captured: dict = {}
        engine = self._engine_capturing_config(captured)
        request_id = str(uuid.uuid4())
        plan = RoutePlan.model_validate(
            {
                "requests": [
                    {"capability": "training", "payload": {"question": "짖어요"},
                     "timeout_ms": None}
                ],
                "handoffs": [],
                "clarify": None,
                "router": "llm",
                "model": "gemini-x",
                "prompt_version": "semantic-router-ko-v7",
            }
        )

        await engine.run(
            route_plan=plan,
            query="우리 개가 짖어요",
            principal=PrincipalContext(subject=str(uuid.uuid4()), kind="APP_USER"),
            request_id=request_id,
        )

        assert captured["run_id"] == uuid.UUID(request_id)
        assert captured["run_name"] == "assistant_query"
        assert captured["metadata"]["router"] == "llm"
        assert captured["metadata"]["router_model"] == "gemini-x"
        assert captured["metadata"]["prompt_version"] == "semantic-router-ko-v7"
        assert captured["tags"] == ["cap:training"]

    @pytest.mark.asyncio
    async def test_메타데이터에_질문_원문이_없다(self) -> None:
        """metadata 는 anonymizer 와 별개 훅이라, 애초에 안 넣는 것이 규칙이다 (D-037)."""
        from daengs_backend.orchestration.contracts import PrincipalContext, RoutePlan

        captured: dict = {}
        engine = self._engine_capturing_config(captured)
        query = "우리 개가 산책 중에 짖어요"
        subject = str(uuid.uuid4())

        await engine.run(
            route_plan=RoutePlan.model_validate(
                {"requests": [{"capability": "training", "payload": {"question": query},
                               "timeout_ms": None}],
                 "handoffs": [], "clarify": None, "router": "deterministic"}
            ),
            query=query,
            principal=PrincipalContext(subject=subject, kind="APP_USER"),
            request_id=str(uuid.uuid4()),
        )

        flattened = repr(captured["metadata"]) + repr(captured["tags"])
        assert query not in flattened
        assert subject not in flattened


class TestReportFeedback:
    """신고 → 트레이스 피드백. **신고가 실패하면 안 된다**는 것이 여기의 요점이다."""

    @pytest.mark.asyncio
    async def test_꺼져_있으면_아무것도_안_한다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from daengs_backend.core import tracing

        monkeypatch.setattr(tracing, "tracing_enabled", lambda: False)
        called: list[object] = []
        from langsmith import run_trees

        monkeypatch.setattr(
            run_trees, "get_cached_client", lambda **kw: called.append(kw) or object()
        )
        await tracing.record_report_feedback(request_id=str(uuid.uuid4()))
        assert called == []

    @pytest.mark.asyncio
    async def test_request_id_가_없으면_넘어간다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """v0.0.0 무상태 대화의 turn 처럼 `request_id` 가 비어 있을 수 있다."""
        from daengs_backend.core import tracing

        monkeypatch.setattr(tracing, "tracing_enabled", lambda: True)
        await tracing.record_report_feedback(request_id=None)
        await tracing.record_report_feedback(request_id="uuid-가-아님")

    @pytest.mark.asyncio
    async def test_보낼_때_사유_원문은_안_실린다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LangSmith 가 알아야 하는 것은 "신고됐다" 뿐이다. 사유는 콘솔의 것이다."""
        from daengs_backend.core import tracing

        sent: dict = {}

        class FakeClient:
            def create_feedback(self, run_id, key="unnamed", **kwargs):
                sent.update({"run_id": run_id, "key": key, **kwargs})

        monkeypatch.setattr(tracing, "tracing_enabled", lambda: True)
        from langsmith import run_trees

        monkeypatch.setattr(run_trees, "get_cached_client", lambda **kw: FakeClient())

        request_id = str(uuid.uuid4())
        await tracing.record_report_feedback(request_id=request_id)

        assert sent["run_id"] == uuid.UUID(request_id)
        assert sent["key"] == tracing.REPORT_FEEDBACK_KEY
        assert sent.get("comment") is None
        # 기본값 10 이면 LangSmith 가 죽었을 때 신고 POST 가 재시도 열 번을 기다린다.
        assert sent["stop_after_attempt"] == 1

    @pytest.mark.asyncio
    async def test_LangSmith_가_죽어도_예외를_안_올린다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """신고는 이미 우리 DB 에 들어갔다. 여기서 실패를 올리면 사용자가 다시 누른다."""
        from daengs_backend.core import tracing

        class ExplodingClient:
            def create_feedback(self, *a, **kw):
                raise RuntimeError("LangSmith unreachable")

        monkeypatch.setattr(tracing, "tracing_enabled", lambda: True)
        from langsmith import run_trees

        monkeypatch.setattr(run_trees, "get_cached_client", lambda **kw: ExplodingClient())

        await tracing.record_report_feedback(request_id=str(uuid.uuid4()))


class TestDefaultOff:
    def test_환경변수가_없으면_꺼져_있다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
            monkeypatch.delenv(name, raising=False)
        # langsmith 가 환경 변수 조회를 lru_cache 로 들고 있어 지운 것이 안 보인다.
        from langsmith import utils as ls_utils

        ls_utils.get_env_var.cache_clear()
        try:
            assert tracing_enabled() is False
            assert configure_tracing() is False
        finally:
            ls_utils.get_env_var.cache_clear()

    def test_꺼져_있으면_클라이언트를_만들지_않는다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """API 키 없이 클라이언트를 만들면 거기서 터진다 — 안 쓰는 사람의 서버가 안 뜬다."""
        for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
            monkeypatch.delenv(name, raising=False)
        from langsmith import run_trees
        from langsmith import utils as ls_utils

        ls_utils.get_env_var.cache_clear()
        created: list[object] = []
        monkeypatch.setattr(
            run_trees, "get_cached_client", lambda **kw: created.append(kw) or object()
        )
        try:
            assert configure_tracing() is False
            assert created == []
        finally:
            ls_utils.get_env_var.cache_clear()
