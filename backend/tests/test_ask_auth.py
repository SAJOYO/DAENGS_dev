"""`POST /ask` 는 로그인한 앱 회원과 관리자만 부를 수 있다 (D-021).

**여기서 보는 것은 문(門)뿐이다.** 응답 계약은 `test_ask_api.py` 가 `daengs_life` 쪽 앱을
세워서 이미 본다 — 그 파일은 인증을 모르고, 알 필요도 없다. `daengs_life.app.main` 은 단독으로
도는 앱이고 거기 `/ask` 는 열려 있다.

문을 **라우터가 아니라 등록 시점에** 거는 것이 이 카드의 설계다 (`main.py` 주석). `ask.router` 가
`daengs_backend.core.deps` 를 import 하면 의존 방향이 뒤집혀, `daengs_life` 가 이 레포의 인증
없이는 못 도는 물건이 된다 (RAG-001 원칙 1 · D-018). 그 배선은 `daengs_backend` 쪽 앱으로만
확인할 수 있고, 이 파일이 그 자리다.

`test_walk_auth.py` 와 같은 모양이지만 **한 가지가 더 있다** — `/ask` 는 의존성이 무겁다.
인증을 통과한 뒤 `Depends(get_encoder)` 가 도는데, `ml` 그룹이 없으면 거기서 503 이 되어야 한다
(500 이 아니라). 그게 D-021 이 개발 PC 에 약속한 것이다.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_life.app import deps
from daengs_life.app.controllers import ask as ask_controller
from daengs_life.rag.stages import embed

QUESTION = {"question": "목줄 안 하면 과태료 얼마인가요?"}


class Reached(Exception):
    """핸들러 본문까지 왔다는 표시. 서비스를 대신한다."""


@pytest.fixture
def client() -> Iterator[TestClient]:
    """**의존성을 갈아끼운 채로 연다.** 안 그러면 인증을 통과한 요청이 실제 모델을 올리고
    실서버 DB 에 붙는다 — `conftest.py` 가 예열을 끈 것과 같은 이유다.

    lifespan 을 태우는 것(`with`)은 그대로 둔다. 등록·미들웨어까지 실제 앱 그대로여야
    "문이 걸렸는가"를 본다고 할 수 있다.
    """
    from daengs_backend.main import app

    app.dependency_overrides[deps.get_encoder] = lambda: deps.Encoder(key="fake", st=object())
    app.dependency_overrides[deps.get_conn] = lambda: object()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def service_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """서비스를 표식으로 갈아끼운다.

    **`test_ask_api.py` 보다 한 층 위를 막는다.** 저쪽은 `generate.ask` 를 막는데, 여기서
    그러면 표식이 안 보인다 — `services/ask.py` 가 상류 실패를 502 로 옮기는 층이라
    `Reached` 를 삼켜 버린다. 컨트롤러가 부르는 `service.ask` 를 막으면 그 위에 아무것도
    없어서 그대로 올라온다. `test_walk_auth.py` 가 `walk_controller.service.walk` 를
    막는 것과 같은 자리다.

    AskOut 을 손으로 지어내지 않는 이유이기도 하다 — 그걸 만들면 이 파일이 응답 계약까지
    흉내 내게 되고, 계약이 바뀔 때 같이 깨진다.
    """
    def boom(*_a, **_k):
        raise Reached

    monkeypatch.setattr(ask_controller.service, "ask", boom)


def _app_token() -> str:
    return create_access_token(uuid.uuid4(), SubjectType.APP)


def _admin_token() -> str:
    return create_access_token(uuid.uuid4(), SubjectType.ADMIN, "ADMIN")


def test_토큰_없이_부르면_401(client: TestClient) -> None:
    assert client.post("/ask", json=QUESTION).status_code == 401


def test_망가진_토큰이면_401(client: TestClient) -> None:
    got = client.post("/ask", json=QUESTION,
                      headers={"Authorization": "Bearer not-a-real-token"})
    assert got.status_code == 401


def test_관리자_토큰이면_핸들러까지_간다(client: TestClient, service_reached: None) -> None:
    """`/walk` 과 같은 판단이다 (메모 ⑦). `post_ask` 도 principal 을 **받지 않으므로**
    관리자 `sub` 가 `app_users` 에 없어서 깨지는 자리가 없다."""
    with pytest.raises(Reached):
        client.post("/ask", json=QUESTION,
                    headers={"Authorization": f"Bearer {_admin_token()}"})


def test_앱_회원_토큰이면_핸들러까지_간다(client: TestClient, service_reached: None) -> None:
    with pytest.raises(Reached):
        client.post("/ask", json=QUESTION,
                    headers={"Authorization": f"Bearer {_app_token()}"})


def test_권한_없는_role_이면_403(client: TestClient) -> None:
    """401 이 아니라 403 인 것까지 고정한다 — 종류는 맞고 권한이 모자란 것이라,
    401 을 주면 프론트(`lib/api.ts`)가 재발급하며 돈다."""
    token = create_access_token(uuid.uuid4(), SubjectType.ADMIN, "NOT_A_ROLE")
    got = client.post("/ask", json=QUESTION, headers={"Authorization": f"Bearer {token}"})
    assert got.status_code == 403


def test_인증이_본문_검증보다_먼저다(client: TestClient) -> None:
    """빈 질문 + 토큰 없음 → 401 이지 422 가 아니다.

    순서가 뒤집히면 로그인하지 않은 사람이 **DTO 의 검증 규칙을 응답으로 떠볼 수 있다.**
    `/walk` 이 좌표 범위에 대해 고정해 둔 것과 같은 자리다.
    """
    assert client.post("/ask", json={"question": ""}).status_code == 401


def test_ml_이_없으면_인증_뒤에_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """**`ml` 그룹이 없는 개발 PC 의 약속** (D-021) — backend 는 뜨고 `/ask` 만 503 이다.

    500 이 아닌 이유는 요청이 틀린 게 아니라 환경이 덜 갖춰진 것이라서다.
    `services/ask.py` 가 `GEMINI_API_KEY` 없음을 503 으로 보내는 것과 같은 규칙이다.

    여기서만 `get_encoder` 를 갈아끼우지 **않는다** — 이 테스트가 보려는 것이 그 함수의
    실패 경로이기 때문이다. 대신 **제일 아래의 로드**를 ImportError 로 만든다. torch 가 깔린
    PC 에서도 같은 결과가 나와야 이 가드가 두 환경에서 같은 것을 지킨다.

    `deps._encoder` 를 통째로 갈아끼우지 않는 것도 그래서다 — 그러면 `lru_cache` 가 아닌
    것이 되어 종료 때 `release_encoder()` 가 `cache_clear` 를 못 찾는다. 캐시는 살려 두고
    앞뒤로 비운다.
    """
    from daengs_backend.main import app

    def no_torch(*_a, **_k):
        raise ImportError("No module named 'torch'")

    deps.release_encoder()
    monkeypatch.setattr(embed, "load_model", no_torch)
    app.dependency_overrides[deps.get_conn] = lambda: object()
    try:
        with TestClient(app) as c:
            got = c.post("/ask", json=QUESTION,
                         headers={"Authorization": f"Bearer {_app_token()}"})
    finally:
        app.dependency_overrides.clear()
        deps.release_encoder()

    assert got.status_code == 503
    assert "torch" in got.json()["detail"]


def test_daengs_life_단독_앱은_그대로_열려_있다() -> None:
    """의존 방향이 안 뒤집혔는지 (D-018 · RAG-001 원칙 1).

    저쪽 앱까지 잠기면 `daengs_life` 가 이 레포의 인증 없이는 못 도는 물건이 된 것이고,
    그건 `main.py` 가 `include_router(dependencies=...)` 를 고른 이유가 사라졌다는 뜻이다.

    lifespan 을 태우지 않으려고 `create_app()` 을 직접 부른다 — `with` 로 열면 저쪽
    lifespan 이 실제 모델을 올린다 (`test_ask_api.py` 와 같은 이유).

    **인코더도 갈아끼운다.** 안 그러면 `ml` 없는 PC 에서 422 대신 503 이 나온다 —
    의존성이 본문 검증보다 먼저 돌기 때문이고, 그건 이 테스트가 보려는 것이 아니다.
    """
    from daengs_life.app.main import create_app

    app = create_app()
    app.dependency_overrides[deps.get_encoder] = lambda: deps.Encoder(key="fake", st=object())
    app.dependency_overrides[deps.get_conn] = lambda: object()
    got = TestClient(app).post("/ask", json={"question": ""})
    assert got.status_code == 422, "본문 검증에 막혀야 한다 — 인증에 막히면 안 된다"
