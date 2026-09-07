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


class TestReviewFindings:
    """코드 리뷰가 잡은 다섯 건. 전부 조용히 되돌아올 수 있는 종류다."""

    def test_반려견_식별자도_지운다(self) -> None:
        """`active_dog_id` 는 회원당 안정적이라 `subject` 와 똑같이 사람을 가리킨다.

        `subject` 만 지우고 이것을 두면 지운 의미가 없다 — 트레이스를 사람 단위로
        묶는 데 그대로 쓸 수 있다.
        """
        dog_id = "9f2b7c1e-0000-4a2b-8c3d-5e6f70819a2b"
        scrubbed = scrub_payload({"context": {"active_dog_id": dog_id, "source": "chat"}})
        assert scrubbed["context"]["active_dog_id"] == REDACTED
        # 답의 입력인 것은 남는다 — 견종·월령은 신원이 아니다.
        assert scrubbed["context"]["source"] == "chat"

    def test_OTLP_주소에_경로가_있다(self) -> None:
        """`/v1/traces` 가 없으면 **트레이스가 한 건도 도착하지 않는다.**

        langsmith 가 이 값을 읽어 `OTLPSpanExporter(endpoint=...)` 로 명시 전달하는데,
        exporter 는 명시 전달된 주소에 경로를 안 붙인다(env 폴백에서만 붙인다).
        베이스 URL 만 주면 전부 `/` 로 POST 돼 404 인데, backend 로그도 collector
        로그도 멀쩡해서 아무도 모른다.
        """
        from pathlib import Path

        compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
        line = next(
            ln for ln in compose.read_text(encoding="utf-8").splitlines()
            if "OTEL_EXPORTER_OTLP_ENDPOINT:" in ln
        )
        assert line.rstrip().endswith("/v1/traces}"), line.strip()

    def test_설정이_틀려도_기동을_막지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`LANGSMITH_TRACING_MODE` 오타 하나로 API 전체가 안 뜨면 안 된다.

        `configure_tracing()` 은 lifespan 의 첫 줄이라, 여기서 예외가 올라가면
        로그인까지 죽는다. 트레이싱이 가질 권한이 아니다.
        """
        from langsmith import run_trees
        from langsmith import utils as ls_utils

        from daengs_backend.core import tracing

        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_TRACING_MODE", "오타난값")
        ls_utils.get_env_var.cache_clear()

        def _explode(**kwargs):
            raise ls_utils.LangSmithUserError("Invalid tracing_mode")

        monkeypatch.setattr(run_trees, "get_cached_client", _explode)
        try:
            assert tracing.configure_tracing() is False
            # 끄고 나가야 한다 — 반쯤 켜진 채로 계측이 돌면 마스킹 없이 나간다.
            assert tracing.tracing_enabled() is False
        finally:
            ls_utils.get_env_var.cache_clear()

    def test_마스킹이_안_걸리면_트레이싱을_끈다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """감지만 하고 계속 돌면 신원·좌표가 그대로 나간다. 로그로 막을 일이 아니다."""
        from langsmith import run_trees
        from langsmith import utils as ls_utils

        from daengs_backend.core import tracing

        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.delenv("LANGSMITH_TRACING_MODE", raising=False)
        ls_utils.get_env_var.cache_clear()

        class ClientWithoutOurMasking:
            _anonymizer = None

        monkeypatch.setattr(
            run_trees, "get_cached_client", lambda **kw: ClientWithoutOurMasking()
        )
        try:
            assert tracing.configure_tracing() is False
            assert tracing.tracing_enabled() is False
        finally:
            ls_utils.get_env_var.cache_clear()

    def test_꺼져_있으면_출력_프로세서가_사본을_안_만든다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """langsmith 는 꺼져 있어도 `process_outputs` 를 부른다 (0.11.2 실측).

        가드가 없으면 트레이싱을 안 켠 서버가 **매 요청마다** 청크 전문을 복사한다.
        """
        from langsmith import utils as ls_utils

        from daengs_training.retrieval.pgvector import _trace_documents

        for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
            monkeypatch.delenv(name, raising=False)
        ls_utils.get_env_var.cache_clear()
        try:
            hits = [{"text": "가" * 5_000, "chunk_id": "c1", "score": 0.9}]
            assert _trace_documents(hits) == {}
        finally:
            ls_utils.get_env_var.cache_clear()


class TestTracingMode:
    """트레이스가 **어디로** 가나. 기본 목적지가 제3자가 아닌 것이 D-054 의 핵심이다."""

    def test_기본값은_langsmith_다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SDK 의 기본값이다. **우리 compose 는 otel 로 덮는다** — 그 사실이 중요해서
        여기서 기본값을 못박는다. 누가 compose 의 그 줄을 지우면 조용히 제3자로 나간다."""
        from langsmith import utils as ls_utils

        from daengs_backend.core import tracing

        monkeypatch.delenv("LANGSMITH_TRACING_MODE", raising=False)
        monkeypatch.delenv("LANGCHAIN_TRACING_MODE", raising=False)
        ls_utils.get_env_var.cache_clear()
        try:
            assert tracing.tracing_mode() == "langsmith"
        finally:
            ls_utils.get_env_var.cache_clear()

    def test_트레이싱_변수가_배포_검증을_깨지_않는다(self) -> None:
        """profiled 서비스의 환경 변수에 `${VAR:?}` 를 쓰면 **모두의 배포가 깨진다.**

        compose 는 프로파일로 거르기 **전에** 보간한다. 배포 워크플로우가
        `docker compose config --quiet` 로 검증하므로(`deploy.yml` "Compose 설정 검증"),
        트레이싱을 안 켜는 서버에서도 그 변수가 없으면 배포가 통째로 실패한다.
        실제로 그렇게 썼다가 잡았다 — 서버에 넣기 전에 잡아서 다행이었을 뿐이다.

        docker 없이 확인할 수 있는 형태로 못박는다.
        """
        import re
        from pathlib import Path

        compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
        text = compose.read_text(encoding="utf-8")
        block = text[text.index("  otel-collector:"):text.index("\n  redis:")]
        required = re.findall(r"\$\{([A-Z_]+):\?", block)
        assert not required, (
            f"otel-collector 가 필수 변수를 요구한다: {required}. "
            "이 서비스는 profiles 뒤에 있어서, 여기서 :? 를 쓰면 트레이싱을 안 켜는 "
            "서버의 `docker compose config` 까지 실패한다 (= 배포 실패)."
        )

    def test_compose_가_otel_로_덮는다(self) -> None:
        """`docker-compose.yml` 의 backend 가 실제로 otel 을 기본으로 주는가.

        문서와 코드가 갈리는 것을 막는다 — `.env` 를 안 고친 서버가 무엇을 쓰는지가
        이 한 줄에 달려 있다.
        """
        from pathlib import Path

        compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
        text = compose.read_text(encoding="utf-8")
        assert "LANGSMITH_TRACING_MODE: ${LANGSMITH_TRACING_MODE:-otel}" in text


class TestReportFeedback:
    """신고 → 트레이스 피드백. **신고가 실패하면 안 된다**는 것이 여기의 요점이다."""

    @pytest.mark.asyncio
    async def test_otel_모드에서는_LangSmith_로_안_부른다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """피드백은 LangSmith REST 기능이라 Cloud Trace 에는 붙을 자리가 없다.

        안 막으면 API 키도 없이 매 신고마다 밖으로 요청이 나가고, 실패를 삼키느라
        아무도 모르는 채 헛돈다.
        """
        from daengs_backend.core import tracing

        called: list[object] = []

        class FakeClient:
            def create_feedback(self, *a, **kw):
                called.append(kw)

        monkeypatch.setattr(tracing, "tracing_enabled", lambda: True)
        monkeypatch.setattr(tracing, "tracing_mode", lambda: "otel")
        from langsmith import run_trees

        monkeypatch.setattr(run_trees, "get_cached_client", lambda **kw: FakeClient())

        await tracing.record_report_feedback(request_id=str(uuid.uuid4()))
        assert called == []

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


class TestEndToEndMasking:
    """**실제로 나가는 바이트**에 마스킹이 걸리는가.

    `TestScrub` 는 함수가 맞게 지운다는 것만 본다. 그 함수가 진짜 전송 경로에
    걸려 있는지는 별개의 사실이고, 안 걸려 있으면 그게 곧 유출이다. 여기서는
    실제 `Client` 를 `configure_tracing()` 이 만드는 그대로 세우고, 진짜 그래프를
    돌리고, `_create_run`(네트워크 직전 · `_run_transform` 마스킹 직후)에서
    payload 를 가로채 확인한다.

    `create_run` → `_run_transform`(마스킹) → `_create_run` 이 langsmith 의 순서라,
    이 지점이 "이 프로세스를 떠나는 것" 의 정확한 경계다.
    """

    @staticmethod
    def _enable(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> list[dict]:
        """트레이싱을 실제로 켜고, 마스킹을 통과한 run payload 를 모으는 리스트를 준다.

        **가로채는 자리가 마스킹 함수의 반환값인 것이 중요하다.** 전송 경로는 하나가
        아니다 — run 생성은 `_run_transform` 을 지나지만 종료(outputs)는 `update_run` 이
        자기 자리에서 따로 마스킹한다. 게다가 기본 설정(`auto_batch_tracing=True`)의
        실제 전송은 백그라운드 스레드의 multipart 배치라, 전송 지점을 잡으면 경로가
        바뀔 때마다 테스트가 헛돈다. `_hide_run_*` 셋은 **마스킹 그 자체**라 무엇이
        나가든 반드시 하나를 지나간다.

        `Client` 가 `__slots__` 이라 인스턴스에 patch 가 안 걸린다. 클래스에 건다.
        """
        from langsmith import run_trees
        from langsmith import utils as ls_utils
        from langsmith.client import Client

        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake_for_tests")
        monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://example.invalid")
        ls_utils.get_env_var.cache_clear()

        sent: list[dict] = []

        def _wrap(method_name: str) -> None:
            real = getattr(Client, method_name)

            def capturing(self, *args, **kwargs):
                masked = real(self, *args, **kwargs)
                sent.append(masked)
                return masked

            monkeypatch.setattr(Client, method_name, capturing)

        for name in ("_run_transform", "_hide_run_inputs", "_hide_run_outputs",
                     "_hide_run_metadata"):
            _wrap(name)

        # **네트워크를 통째로 막는다.** `request_with_retries` 가 이 클라이언트의 유일한
        # HTTP 통로다. 전송 메서드를 하나씩 막으면 배치·압축 경로가 갈려서 새는데,
        # 실제로 백그라운드 스레드가 그리로 빠져나가는 것을 확인했다. 단위 테스트가
        # 밖으로 나가면 CI 에서 DNS 타임아웃만큼 느려지고 로그도 덮인다.
        #
        # 이 테스트가 보는 것은 "무엇이 나갈 뻔했나" 이지 전송이 아니다.
        def _no_network(self, *args, **kwargs):
            raise AssertionError("이 테스트는 네트워크로 나가지 않는다")

        monkeypatch.setattr(Client, "request_with_retries", _no_network)
        monkeypatch.setattr(Client, "_multipart_ingest_ops", lambda self, *a, **kw: None)
        monkeypatch.setattr(Client, "_send_multipart_req", lambda self, *a, **kw: None)
        monkeypatch.setattr(Client, "_send_compressed_multipart_req",
                            lambda self, *a, **kw: None)
        monkeypatch.setattr(Client, "_create_run", lambda self, *a, **kw: None)

        # 전역 캐시를 비워야 `configure_tracing()` 이 자기 kwargs 로 클라이언트를 만든다.
        monkeypatch.setattr(run_trees, "_CLIENT", None, raising=False)
        assert configure_tracing() is True

        # **테스트가 끝나기 전에 큐를 닫는다.** 이 클라이언트는 백그라운드 스레드로
        # 배치를 보내는데, 그 플러시는 monkeypatch 가 **풀린 뒤**(세션 종료) 돈다 —
        # 그때는 위의 네트워크 차단이 이미 사라져 진짜로 밖으로 나간다. 차단이 살아
        # 있는 동안 여기서 닫아 두면 나중에 보낼 것이 남지 않는다.
        #
        # `addfinalizer` 는 LIFO 라 monkeypatch 의 되돌리기보다 **먼저** 돈다.
        client = run_trees.get_cached_client()
        request.addfinalizer(lambda: client.cleanup(timeout=5))
        return sent

    @pytest.mark.asyncio
    async def test_실제_그래프를_돌리면_신원과_좌표만_지워져_나간다(
        self, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
    ) -> None:
        from langsmith import utils as ls_utils

        from daengs_backend.orchestration.contracts import (
            CapabilityName,
            CapabilityResult,
            CapabilityStatus,
            PrincipalContext,
            RoutePlan,
        )
        from daengs_backend.orchestration.graph import OrchestrationEngine

        sent = self._enable(monkeypatch, request)
        try:
            class FakeTrainingAdapter:
                capability = CapabilityName.TRAINING

                async def run(self, request, *, request_id: str) -> CapabilityResult:
                    return CapabilityResult(
                        capability=self.capability,
                        status=CapabilityStatus.OK,
                        data={"answer": "짖음 교육 방법입니다"},
                        elapsed_ms=1,
                    )

            query = "우리 개가 산책 중에 짖어요"
            subject = "8f14e45f-ceea-467a-9f0e-2b8e1a0d3c44"
            engine = OrchestrationEngine({CapabilityName.TRAINING: FakeTrainingAdapter()})
            await engine.run(
                route_plan=RoutePlan.model_validate(
                    {
                        "requests": [{"capability": "training",
                                      "payload": {"question": query}, "timeout_ms": None}],
                        "handoffs": [], "clarify": None, "router": "llm",
                    }
                ),
                query=query,
                principal=PrincipalContext(subject=subject, kind="APP_USER"),
                request_id=str(uuid.uuid4()),
                context={"location": {"lat": 37.566826, "lon": 126.978656}},
            )

            assert sent, "트레이싱을 켰는데 나간 run 이 하나도 없다"
            blob = repr(sent)

            # 1) 지워져야 하는 것 — **문자열 전체에서** 찾는다. 어느 한 필드만 보면
            #    다른 노드의 입력으로 같은 값이 새는 것을 놓친다.
            assert subject not in blob, "회원 식별자가 그대로 나갔다"
            assert "37.566826" not in blob, "정밀 좌표가 그대로 나갔다"
            assert "126.978656" not in blob

            # 2) 남아야 하는 것 — 이게 없으면 트레이싱을 켤 이유가 없다.
            assert query in blob, "질문 원문이 안 나갔다 (그럼 진단이 불가능하다)"
            assert REDACTED in blob
            assert "37.57" in blob, "좌표가 반올림되어 남아야 한다 (통째로 지우지 않는다)"
        finally:
            ls_utils.get_env_var.cache_clear()

    @pytest.mark.asyncio
    async def test_훈련_RAG_계측이_같은_클라이언트를_탄다(
        self, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
    ) -> None:
        """`@traceable` 은 LangGraph 와 다른 경로인데, 폴백이 같은 전역이라 함께 덮인다.

        그 사실이 `core/tracing.py` 의 "주입점이 하나" 주장을 떠받치므로 여기서 못박는다.
        """
        from langsmith import traceable
        from langsmith import utils as ls_utils

        sent = self._enable(monkeypatch, request)
        try:
            @traceable(run_type="retriever", name="pgvector_search")
            def search(question: str) -> list[dict]:
                return [{"text": "짖음은 요구성 짖음일 수 있습니다", "score": 0.88}]

            search("우리 개가 짖어요")

            assert sent, "@traceable 이 트레이싱을 켰는데도 아무것도 안 보냈다"
            names = [r.get("name") for r in sent if isinstance(r, dict)]
            assert "pgvector_search" in names
            assert "짖음은 요구성 짖음일 수 있습니다" in repr(sent), "청크 전문이 나가야 한다"
        finally:
            ls_utils.get_env_var.cache_clear()


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
