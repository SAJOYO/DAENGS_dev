"""기동 때의 예열과 **모델 불일치 경고** (D-021).

`/ask` 가 조용히 틀릴 수 있는 자리는 하나뿐이다 — **서빙 키와 코퍼스의 임베딩 모델이 다를 때.**
문서 벡터와 질의 벡터가 다른 모델이면 두 벡터가 다른 공간에 있어 코사인이 무의미해지는데,
세 모델의 차원이 전부 1024 라 예외가 **하나도** 안 난다. 그럴듯한 순위가 그냥 나온다.

`#34` 가 `qwen3` 로 적재하고 이 카드가 기본값을 바꾸므로 그 창이 실제로 열린다. 그래서
`deps.warn_if_corpus_uses_another_model` 이 기동 때 한 번 소리를 내고, 이 파일이 그것을 지킨다.

**lifespan 을 통해 보지 않는다.** 예열은 백그라운드 스레드라 lifespan 으로 관찰하면 스레드와
취소가 끼어 느리고 불안정해진다. 함수를 직접 부르고, *배선*(설정에 따라 부르는지 마는지)만
따로 본다.
"""
from __future__ import annotations

import logging
import threading

import pytest
from fastapi.testclient import TestClient

from daengs_backend.config import settings
from daengs_life.app import deps
from daengs_life.rag.stages import embed, load


class FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def corpus(monkeypatch: pytest.MonkeyPatch):
    """`documents` 를 흉내 낸다. 돌려주는 것은 (행 목록을 정하는 함수, 열린 커넥션 목록)."""
    conns: list[FakeConn] = []

    def _set(rows, *, connect_fails=False):
        def connect():
            if connect_fails:
                raise OSError("connection refused")
            c = FakeConn()
            conns.append(c)
            return c

        monkeypatch.setattr(load, "connect", connect)
        monkeypatch.setattr(load, "existing_models", lambda _c: rows)
        return conns

    return _set


# ---------------------------------------------------------------- 대조 (조용히 틀리는 자리)
def test_불일치면_두_이름을_다_말한다(corpus, caplog: pytest.LogCaptureFixture) -> None:
    """**어느 쪽을 고쳐야 하는지는 둘을 다 봐야 정해진다.** 서빙만 찍으면 재적재를 할지
    설정을 바꿀지 판단할 수 없다."""
    corpus([("bge-m3", 1407)])
    with caplog.at_level(logging.WARNING):
        deps.warn_if_corpus_uses_another_model("qwen3-embedding-0.6b")

    msg = caplog.text
    assert "qwen3-embedding-0.6b" in msg and "bge-m3" in msg
    assert "1407" in msg, "몇 건이 그 모델로 들어 있는지까지 봐야 규모를 안다"


def test_일치하면_경고하지_않는다(corpus, caplog: pytest.LogCaptureFixture) -> None:
    """정상 경로에서 경고가 나면 **경고가 무시되기 시작한다.** 그러면 진짜 불일치도 묻힌다."""
    corpus([("qwen3-embedding-0.6b", 1407)])
    with caplog.at_level(logging.WARNING):
        deps.warn_if_corpus_uses_another_model("qwen3-embedding-0.6b")

    assert caplog.records == []


def test_코퍼스가_비면_그걸_말한다(corpus, caplog: pytest.LogCaptureFixture) -> None:
    """이 카드를 `#34` 보다 먼저 머지하면 여기로 온다. `/ask` 는 404 만 내는데, 그 이유가
    "질문이 나빠서"가 아니라 "적재가 아직"이라는 것을 로그가 말해 줘야 한다."""
    corpus([])
    with caplog.at_level(logging.WARNING):
        deps.warn_if_corpus_uses_another_model("qwen3-embedding-0.6b")

    assert "비어" in caplog.text


def test_DB_가_안_되면_경고만_하고_넘어간다(corpus, caplog: pytest.LogCaptureFixture) -> None:
    """**대조 실패로 앱을 못 세우면 안 된다.** 이건 확인이지 기동 조건이 아니다 —
    `Cache` 가 Redis 없이 뜨는 것과 같은 태도다."""
    corpus([], connect_fails=True)
    with caplog.at_level(logging.WARNING):
        deps.warn_if_corpus_uses_another_model("qwen3-embedding-0.6b")

    assert "확인하지 못했다" in caplog.text


def test_커넥션을_반드시_닫는다(corpus) -> None:
    """요청당 하나인 `get_conn` 과 달리 이건 기동 때 한 번이다. 그래도 안 닫으면
    풀에 하나가 영영 남는다 — 리로드가 잦은 개발 모드에서 그게 쌓인다."""
    conns = corpus([("qwen3-embedding-0.6b", 1)])
    deps.warn_if_corpus_uses_another_model("qwen3-embedding-0.6b")
    assert conns and all(c.closed for c in conns)


# ---------------------------------------------------------------- 예열
def test_예열이_실패해도_던지지_않는다(monkeypatch: pytest.MonkeyPatch,
                                      caplog: pytest.LogCaptureFixture) -> None:
    """부르는 쪽이 lifespan 이다. 여기서 예외가 나가면 **앱이 아예 안 뜬다** — `ml` 없는
    개발 PC 에서 로그인도 `/walk` 도 못 보게 된다는 뜻이고, 그게 D-021 이 막은 것이다."""
    def no_torch(*_a, **_k):
        raise ImportError("No module named 'torch'")

    deps.release_encoder()
    monkeypatch.setattr(embed, "load_model", no_torch)
    try:
        with caplog.at_level(logging.WARNING):
            deps.warm_up_encoder()          # 던지면 여기서 테스트가 깨진다
    finally:
        deps.release_encoder()

    assert "torch" in caplog.text
    assert "503" in caplog.text, "왜 /ask 만 죽는지를 로그가 말해 줘야 한다"


def test_예열에_성공하면_대조까지_간다(monkeypatch: pytest.MonkeyPatch, corpus,
                                       caplog: pytest.LogCaptureFixture) -> None:
    """예열과 대조를 한 함수에 둔 이유 — **모델 키는 올려 봐야 확정된다.** 따로 두면
    설정만 읽고 대조하게 되고, 그러면 로드가 실패한 프로세스도 "일치한다"고 말한다."""
    corpus([("bge-m3", 5)])
    monkeypatch.setattr(embed, "load_model", lambda *_a, **_k: object())
    deps.release_encoder()
    try:
        with caplog.at_level(logging.WARNING):
            deps.warm_up_encoder()
    finally:
        deps.release_encoder()

    assert "bge-m3" in caplog.text, "예열이 성공했으면 대조 경고까지 나와야 한다"


def test_모델을_한_벌만_올린다(monkeypatch: pytest.MonkeyPatch, corpus) -> None:
    """**동시 미스가 두 벌을 올리면 그 순간 RAM 이 2배다** — 상주 2.4GB 가 이 카드의 유일한
    상시 비용이라(D-021) 그게 서버를 스왑으로 민다. `lru_cache` 는 캐시만 스레드 안전하고
    동시 미스는 막지 않아서, `deps._ENCODER_LOCK` 이 그 자리를 맡는다.

    예열(백그라운드)과 요청(`/ask`)이 정확히 그렇게 부딪힌다.
    """
    corpus([("qwen3-embedding-0.6b", 1)])
    calls: list[int] = []
    started = threading.Event()

    def slow_load(*_a, **_k):
        calls.append(1)
        started.set()
        threading.Event().wait(0.3)     # 로드가 도는 동안 다른 스레드가 들어오게 둔다
        return object()

    monkeypatch.setattr(embed, "load_model", slow_load)
    deps.release_encoder()
    try:
        warm = threading.Thread(target=deps.warm_up_encoder)
        warm.start()
        assert started.wait(5), "예열이 시작되지 않았다"
        got = deps.get_encoder()        # 로드가 도는 중에 들어온 '요청'
        warm.join(10)
    finally:
        deps.release_encoder()

    assert len(calls) == 1, f"모델을 {len(calls)}벌 올렸다 — 락이 안 걸렸다"
    assert got.key == "qwen3-embedding-0.6b"


# ---------------------------------------------------------------- 배선 (설정으로 끌 수 있는가)
def test_설정이_꺼져_있으면_예열하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`conftest.py` 가 이 값을 끈다. 안 끄면 `ml` 이 깔린 PC 에서 테스트마다 1.2GB 를
    올리고 **실서버 DB 에까지 붙는다.**"""
    import daengs_backend.main as backend_main

    called: list[int] = []
    monkeypatch.setattr(backend_main, "warm_up_encoder", lambda: called.append(1))
    monkeypatch.setattr(settings, "warm_up_encoder", False)
    with TestClient(backend_main.app):
        pass

    assert called == []


def test_설정이_켜져_있으면_백그라운드로_예열한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**동기로 부르면 안 된다** — 이 프로세스에는 로그인·`/walk`·`/training` 이 같이 살고,
    가중치를 올리는 5~7초 동안 API 전체가 502 다.

    여기서 "백그라운드"를 증명하는 방법: 예열을 붙잡아 둔 채로 앱이 다른 요청에 답하는지
    본다. 동기였다면 lifespan 이 `yield` 에 닿지 못해 **아무 요청도** 안 받는다.

    `/health` 를 안 쓰는 이유 — 저건 DB 를 만진다. 테스트 환경에는 DB 가 없어서 무엇을
    보는지가 흐려진다. `/openapi.json` 은 의존성이 없어 "루프가 살아 있는가"만 남는다.
    """
    import daengs_backend.main as backend_main

    started, release = threading.Event(), threading.Event()

    def blocking_warm_up() -> None:
        started.set()
        release.wait(10)

    monkeypatch.setattr(backend_main, "warm_up_encoder", blocking_warm_up)
    monkeypatch.setattr(settings, "warm_up_encoder", True)
    try:
        with TestClient(backend_main.app) as c:
            assert started.wait(5), "예열이 시작되지 않았다"
            assert c.get("/openapi.json").status_code == 200, "예열이 API 를 막고 있다"
    finally:
        release.set()
