"""`GET /life/walk-conditions` 은 로그인한 앱 회원과 관리자만 부를 수 있다 (#23 · #30).

**여기서 보는 것은 문(門)뿐이다.** 응답 계약과 저하 경로는 `test_walk_api.py` 가
`daengs_life` 쪽 앱을 세워서 이미 본다 — 그 파일은 인증을 모르고, 알 필요도 없다.
`daengs_life.app.main` 은 단독으로 도는 앱이고 거기 `/life/walk-conditions` 은 열려 있다.

문을 **라우터가 아니라 등록 시점에** 거는 것이 이 카드의 설계다 (`main.py` 주석).
그래서 그 배선이 실제로 걸렸는지는 `daengs_backend` 쪽 앱으로만 확인할 수 있고,
이 파일이 그 자리다.

판정 서비스는 부르지 않는다. 인증을 통과했다는 것만 확인하면 되고, 실제로 부르면
공공 API 를 타거나(키가 있는 PC) 픽스처 배선을 통째로 복제해야 한다.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_life.app.controllers import walk as walk_controller

SEOUL = {"lat": 37.4979, "lon": 127.0276}


class Reached(Exception):
    """핸들러 본문까지 왔다는 표시. 서비스를 대신한다."""


@pytest.fixture
def client() -> TestClient:
    from daengs_backend.main import app

    # raise_server_exceptions=True(기본) 라 `Reached` 가 그대로 올라온다.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def service_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """판정 서비스를 표식으로 갈아끼운다.

    컨트롤러가 `from ... import walk as service` 후 `service.walk(...)` 로 부르므로
    모듈 속성을 바꾸면 잡힌다. WalkOut 을 손으로 지어내지 않는 이유이기도 하다 —
    그걸 만들면 이 파일이 응답 계약까지 흉내 내게 되고, 계약이 바뀔 때 같이 깨진다.
    """
    def boom(*_a, **_k):
        raise Reached

    monkeypatch.setattr(walk_controller.service, "walk", boom)


def _app_token() -> str:
    return create_access_token(uuid.uuid4(), SubjectType.APP)


def _admin_token() -> str:
    return create_access_token(uuid.uuid4(), SubjectType.ADMIN, "ADMIN")


def test_토큰_없이_부르면_401(client: TestClient) -> None:
    assert client.get("/life/walk-conditions", params=SEOUL).status_code == 401


def test_망가진_토큰이면_401(client: TestClient) -> None:
    # 헤더 값은 ascii 여야 한다 (httpx 가 한글을 못 싣는다). 어차피 여기서 보는 것은
    # "우리 토큰이 아니다" 이고, 그건 아무 ascii 문자열이나 똑같이 만족한다.
    got = client.get("/life/walk-conditions", params=SEOUL,
                     headers={"Authorization": "Bearer not-a-real-token"})
    assert got.status_code == 401


def test_관리자_토큰이면_핸들러까지_간다(client: TestClient, service_reached: None) -> None:
    """**`#23` 의 결정을 `#30` 이 뒤집은 자리다.**

    저 카드는 여기서 401 을 못박아 뒀다 — `current_app_user` 의 거울상 규칙이었다.
    관리자도 콘솔에서 산책 판정을 확인할 수 있어야 해서 문을 넓혔다. `#23` 이
    틀렸던 것이 아니라 요구가 바뀐 것이다.

    넓혀도 되는 이유는 `get_walk` 이 principal 을 **안 쓰기** 때문이다. 좌표만 보고
    답하므로 관리자 `sub` 가 `app_users` 에 없어서 깨지는 자리가 없다.
    """
    with pytest.raises(Reached):
        client.get("/life/walk-conditions", params=SEOUL,
                   headers={"Authorization": f"Bearer {_admin_token()}"})


def test_권한_없는_role_이면_403(client: TestClient) -> None:
    """모르는 role 은 아무 권한도 못 받는다 (`ROLE_PERMISSIONS` 의 fail-closed).

    401 이 아니라 403 인 것까지 고정한다 — 종류는 맞고 권한이 모자란 것이라,
    401 을 주면 프론트(`lib/api.ts`)가 재발급하며 돈다.
    """
    token = create_access_token(uuid.uuid4(), SubjectType.ADMIN, "NOT_A_ROLE")
    got = client.get("/life/walk-conditions", params=SEOUL,
                     headers={"Authorization": f"Bearer {token}"})
    assert got.status_code == 403


def test_앱_회원_토큰이면_핸들러까지_간다(client: TestClient, service_reached: None) -> None:
    with pytest.raises(Reached):
        client.get("/life/walk-conditions", params=SEOUL,
                   headers={"Authorization": f"Bearer {_app_token()}"})


def test_인증이_좌표_검증보다_먼저다(client: TestClient) -> None:
    """범위 밖 좌표 + 토큰 없음 → 401 이지 422 가 아니다.

    순서가 뒤집히면 로그인하지 않은 사람이 **좌표 범위(⑥ 의 33~39 / 124~132)를
    응답으로 떠볼 수 있다.** 사소해 보이지만, 인증 전에 도는 검증이 하나라도 있으면
    거기부터 정보가 샌다.
    """
    got = client.get("/life/walk-conditions", params={"lat": 0.0, "lon": 0.0})
    assert got.status_code == 401


def test_daengs_life_단독_앱은_그대로_열려_있다() -> None:
    """의존 방향이 안 뒤집혔는지 (D-018 · RAG-001 원칙 1).

    저쪽 앱까지 잠기면 `daengs_life` 가 이 레포의 인증 없이는 못 도는 물건이 된 것이고,
    그건 `main.py` 가 `include_router(dependencies=...)` 를 고른 이유가 사라졌다는 뜻이다.
    """
    from daengs_life.app.main import create_app

    got = TestClient(create_app()).get("/life/walk-conditions", params={"lat": 0.0, "lon": 0.0})
    assert got.status_code == 422, "좌표 검증에 막혀야 한다 — 인증에 막히면 안 된다"
