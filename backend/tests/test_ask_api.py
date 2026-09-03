"""`POST /life/ask` — API 레벨 (RAG-027 · RAG-028).

**모델도 DB 도 Gemini 도 부르지 않는다.** `deps.py` 가 존재하는 두 번째 이유(*"테스트가 여기를
갈아끼운다"*)를 그대로 쓴다 — `dependency_overrides` 로 인코더와 커넥션을 갈아끼우고, 생성은
`services.ask` 가 부르는 `generate.ask` 를 monkeypatch 로 막는다.

`test_walk_api.py` 가 검문소 D 를 API 레벨에서 다시 돌린 것과 같은 자리이지만, **여기서 검문소④를
다시 돌리지는 않는다.** 검문소④는 CLI(`rag generate --questions`)에 있고, 그것이 RAG-028 ③이 조립을
`rag` 에 둔 이유다. 여기서 붙잡는 것은 **계약과 경계**다.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from daengs_life.app import deps
from daengs_life.app.main import create_app
from daengs_life.app.services import ask as service
from daengs_life.rag.stages.generate import Answer
from daengs_life.rag.stages.search import Hit

HITS = [
    Hit(rank=1, score=0.63, chunk_id="easylaw-pet-2-2-1-qna__20260819#qa-3",
        citation="반려견 목줄 착용", citation_url="https://www.easylaw.go.kr/…",
        section=None, document_title="반려동물과 생활하기",
        content="안전조치를 하지 않은 경우에는 50만원 이하의 과태료가 부과됩니다"
                "(「동물보호법」 제101조제4항제4호).", part=None),
]

ANSWER = Answer(
    question="목줄 안 하면 과태료 얼마인가요?",
    text="50만원 이하의 과태료가 부과됩니다. 「동물보호법」 제101조 제4항 제4호 [1]",
    hits=HITS, model="gemini-fake", embedding_model="bge-m3",
    cited=["제101조"], ungrounded=[],
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """앱 하나 + 갈아끼운 의존성. **lifespan 을 태우지 않으려고 `create_app()` 을 직접 부른다** —
    `TestClient(app)` 을 `with` 로 열면 lifespan 이 실제 모델을 올린다(6.5GB)."""
    app = create_app()
    app.dependency_overrides[deps.get_encoder] = lambda: deps.Encoder(key="bge-m3", st=object())
    app.dependency_overrides[deps.get_conn] = lambda: object()
    monkeypatch.setattr(service.generate, "ask", lambda *a, **k: ANSWER)
    return TestClient(app)


def test_answers_with_its_evidence(client: TestClient) -> None:
    """**RAG-028 ② — 답변만 주면 검문소④를 만들 수 없다.** 무엇을 컨텍스트로 줬는지 함께 말한다."""
    r = client.post("/life/ask", json={"question": "목줄 안 하면 과태료 얼마인가요?"})
    assert r.status_code == 200
    d = r.json()
    assert d["answer"] and d["hits"] and d["cited"] == ["제101조"] and d["ungrounded"] == []


def test_kpi_fields_survive_the_dto(client: TestClient) -> None:
    """KPI 는 **출처 링크 + 조항 번호**다. 둘 중 하나라도 DTO 에서 새면 제품이 성립하지 않는다."""
    h = client.post("/life/ask", json={"question": "q"}).json()["hits"][0]
    assert h["citation"] and h["citation_url"]


def test_content_is_not_truncated(client: TestClient) -> None:
    """근거 본문을 자르지 않는다 (RAG-028 ②). 자르면 인용이 옳은 읽기인지 판정할 수 없다."""
    h = client.post("/life/ask", json={"question": "q"}).json()["hits"][0]
    assert h["content"] == HITS[0].content


def test_both_model_names_are_reported(client: TestClient) -> None:
    """랩 비교는 **둘 다** 있어야 성립한다 — 답을 만든 모델과 검색에 쓴 모델 (RAG-028 ⑥)."""
    d = client.post("/life/ask", json={"question": "q"}).json()
    assert d["model"] == "gemini-fake" and d["embedding_model"] == "bge-m3"


def test_empty_question_is_rejected_by_the_contract(client: TestClient) -> None:
    """검증은 DTO 가 한다 — 컨트롤러에 로직을 넣지 않기 위해서다 (RAG-027 강제 규칙)."""
    assert client.post("/life/ask", json={"question": ""}).status_code == 422


def test_no_evidence_means_no_answer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """**근거 0건이면 생성하지 않는다.** 빈 컨텍스트로 Gemini 에 넘기면 그것은 검색 결과 위의
    답이 아니라 모델의 기억이고, KPI(출처 링크 + 조항 번호)가 성립할 수 없다.

    ⚠️ 이것은 RAG-029(근거가 *약할* 때의 거부)가 아니라 **아예 없는** 경우의 처리다.
    """
    empty = Answer(question="q", text="", hits=[], model="m", embedding_model="bge-m3")
    monkeypatch.setattr(service.generate, "ask", lambda *a, **k: empty)
    assert client.post("/life/ask", json={"question": "q"}).status_code == 404


def test_missing_key_is_503_not_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """키가 없는 것은 **설정 문제지 요청 문제가 아니다.** 500 으로 내면 클라이언트가 재시도한다."""
    def boom(*a, **k):
        raise RuntimeError("GEMINI_API_KEY 가 없다")

    monkeypatch.setattr(service.generate, "ask", boom)
    r = client.post("/life/ask", json={"question": "q"})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]


def test_upstream_failure_says_which_upstream(client: TestClient,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """502 로 뭉뚱그리되 **어느 상류인지는 남긴다** — 로그 없이 DB 와 Gemini 를 못 가르면 안 된다."""
    def boom(*a, **k):
        raise TimeoutError("연결 시간 초과")

    monkeypatch.setattr(service.generate, "ask", boom)
    r = client.post("/life/ask", json={"question": "q"})
    assert r.status_code == 502 and "TimeoutError" in r.json()["detail"]


def test_gemini_timeout_is_504_not_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """**우리가 건 타임아웃**은 504 다 (D-021). 502("상류가 죽었다")와 갈라 둔다.

    ⚠️ 바로 위 테스트와 헷갈리기 쉬운 자리라 같이 읽어야 한다. 저기 쓰인 것은 **내장**
    `TimeoutError` 이고 그건 502 로 남는다 — 소켓이 끊긴 것일 수도, DB 가 안 받는 것일
    수도 있어서 *생성이 느렸다* 고 말할 근거가 없다. 여기서 504 로 올리는 것은
    `_client()` 가 `HttpOptions(timeout=...)` 로 **직접 건** 시계가 울린 경우뿐이고,
    그 타입은 전송 계층(`httpx`)의 것이다.

    `_TIMEOUTS` 가 `except Exception` 보다 **위**에 있어야 이 구분이 산다. 순서가 뒤집히면
    조용히 502 로 먹히고, 검문소(와 나중의 게이트웨이)가 "느린 것"과 "죽은 것"을 못 가른다.
    """
    def boom(*a, **k):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(service.generate, "ask", boom)
    r = client.post("/life/ask", json={"question": "q"})
    assert r.status_code == 504
    assert "30" in r.json()["detail"], "몇 초 안에 안 왔는지를 말해 준다"


def test_the_timeout_reaches_the_client_in_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """**단위를 여기서 한 번 고정한다.** google-genai 의 `HttpOptions.timeout` 은 초가 아니라
    밀리초다. 30 을 넣으면 30밀리초가 되어 전부 504 가 되는데, 그 실수는 눈으로 안 보인다 —
    설정 이름이 `gemini_timeout_ms` 인 것과 이 테스트가 한 쌍이다.

    변환을 끼우지 않는 것도 같이 고정된다. 어딘가에서 초↔밀리초를 바꾸기 시작하면
    설정과 SDK 가 서로를 믿어야 한다.
    """
    from google import genai

    from daengs_life.rag.core import config
    from daengs_life.rag.stages import generate

    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(genai, "Client", FakeClient)
    generate._client(api_key="키가-있는-척")
    assert seen["http_options"].timeout == config.settings.gemini_timeout_ms
    assert seen["http_options"].timeout == 30_000


def test_walk_still_registered() -> None:
    """`/life/ask` 를 붙이면서 `main.py` 에서 겹치는 것은 **등록 한 줄**이어야 한다 (RAG-027 마지막 절).
    파트②의 엔드포인트가 사라지면 그 약속이 깨진 것이다.

    **경로 문자열을 그대로 적는 것이 이 가드의 값이다** (A4 · #176). 경로를 바꾸면 여기서
    걸리므로, 이름만 바꾸고 한쪽을 빠뜨리는 일이 조용히 지나가지 않는다."""
    # `app.routes` 는 이 FastAPI 버전에서 지연 등록(`_IncludedRouter`)이라 경로가 안 보인다.
    # OpenAPI 스키마를 보면 **실제로 공개되는 계약**을 보게 되므로 이쪽이 더 옳은 검사이기도 하다.
    paths = create_app().openapi()["paths"]
    assert "/life/walk-conditions" in paths and "/life/ask" in paths
    # 옛 경로는 리다이렉트 없이 사라졌다 (#176) — 소비자가 콘솔 하나뿐이라 같이 나간다.
    assert "/walk" not in paths and "/ask" not in paths
