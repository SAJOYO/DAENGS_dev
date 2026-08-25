"""routers/auth.py — 쿠키와 상태 코드.

services 쪽 판단은 test_auth_service.py 가 봅니다. 여기서 보는 것은 **HTTP 경계**입니다.

    - 토큰이 쿠키로만 나가는가 (본문에 새지 않는가)
    - httpOnly / SameSite 가 실제로 붙는가
    - 실패가 401 인가 429 인가, 권한 부족이 403 인가

쿠키 속성이 빠지는 것은 **테스트 없이는 안 보이는** 종류의 사고입니다.
httponly 가 빠져도 로그인은 정상으로 보이고, XSS 가 나야 드러납니다.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import (
    ACCESS_COOKIE,
    CurrentAdmin,
    Perm,
    Principal,
    require,
)
from daengs_backend.core.password import hash_password
from daengs_backend.core.token import hash_refresh_token
from daengs_backend.routers import auth as auth_router
from daengs_backend.routers.auth import REFRESH_COOKIE
from daengs_backend.services import login_attempts
from fakes import PASSWORD, FakeAdmin, FakeSession, Store, install


@pytest.fixture
def admin() -> FakeAdmin:
    return FakeAdmin(password_hash=hash_password(PASSWORD))


@pytest.fixture
def store(admin: FakeAdmin, monkeypatch: pytest.MonkeyPatch) -> Store:
    return install(Store(admin), monkeypatch)


@pytest.fixture(autouse=True)
def _clean_attempts() -> None:
    """실패 카운터는 모듈 전역이라 테스트끼리 샙니다."""
    login_attempts.reset_all()


@pytest.fixture
def app() -> FastAPI:
    """auth 라우터만 붙인 앱. main.py 를 쓰지 않는 이유는 DB 엔진 때문입니다.

    권한 의존성을 확인할 자리가 없어서 시험용 엔드포인트를 두 개 답니다.
    """
    test_app = FastAPI()
    test_app.include_router(auth_router.router)

    @test_app.get("/_pii")
    async def _pii(
        admin: Annotated[Principal, Depends(require(Perm.PII_READ))],
    ) -> dict[str, str]:
        return {"ok": admin.role}

    @test_app.get("/_manage")
    async def _manage(
        admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))],
    ) -> dict[str, str]:
        return {"ok": admin.role}

    @test_app.get("/_any")
    async def _any(admin: CurrentAdmin) -> dict[str, str]:
        return {"ok": str(admin.admin_id)}

    async def _fake_session() -> FakeSession:
        return FakeSession()

    test_app.dependency_overrides[get_session] = _fake_session
    return test_app


@pytest.fixture
def client(app: FastAPI, store: Store) -> TestClient:
    return TestClient(app)


def _login(client: TestClient, password: str = PASSWORD, login_id: str = "daengs"):  # noqa: ANN202
    return client.post(
        "/auth/login", json={"login_id": login_id, "password": password}
    )


class TestLoginRoute:
    def test_성공하면_쿠키_두_개(self, client: TestClient) -> None:
        res = _login(client)

        assert res.status_code == 200
        assert ACCESS_COOKIE in res.cookies
        assert REFRESH_COOKIE in res.cookies

    def test_토큰이_본문에_새지_않는다(self, client: TestClient) -> None:
        """httpOnly 로 둔 이유가 응답 본문 때문에 무의미해지면 안 됩니다."""
        res = _login(client)
        body = res.text

        assert res.cookies[ACCESS_COOKIE] not in body
        assert res.cookies[REFRESH_COOKIE] not in body

    def test_httponly_와_samesite_가_붙는다(self, client: TestClient) -> None:
        """빠져도 로그인은 정상으로 보입니다. XSS 나 CSRF 가 나야 드러납니다."""
        res = _login(client)

        cookies = [
            h for h in res.headers.get_list("set-cookie") if "daengs_" in h
        ]
        assert len(cookies) == 2
        for header in cookies:
            assert "HttpOnly" in header
            assert "SameSite=lax" in header.replace("samesite", "SameSite")

    def test_http_라서_Secure_는_안_붙는다(self, client: TestClient) -> None:
        """settings.cookie_secure 기본값이 False 입니다.

        지금 켜면 http 에서 쿠키가 아예 안 실립니다. HTTPS 로 옮길 때 켜야 합니다.
        """
        res = _login(client)

        for header in res.headers.get_list("set-cookie"):
            if "daengs_" in header:
                assert "Secure" not in header

    def test_응답에_만료_시각이_있다(self, client: TestClient) -> None:
        """프론트가 미리 재발급을 걸 수 있어야 합니다. 쿠키는 JS 가 못 읽습니다."""
        body = _login(client).json()

        assert body["access_expires_at"]
        assert body["refresh_expires_at"]
        assert body["role"] == "ADMIN"

    def test_틀린_비밀번호는_401_이고_쿠키가_없다(self, client: TestClient) -> None:
        res = _login(client, password="wrong")

        assert res.status_code == 401
        assert ACCESS_COOKIE not in res.cookies

    def test_없는_아이디도_같은_응답이다(self, client: TestClient) -> None:
        """메시지가 다르면 어느 아이디가 존재하는지 알아낼 수 있습니다."""
        wrong_pw = _login(client, password="wrong")
        no_user = _login(client, login_id="nobody", password="wrong")

        assert wrong_pw.status_code == no_user.status_code == 401
        assert wrong_pw.json() == no_user.json()

    def test_빈_값은_422(self, client: TestClient) -> None:
        res = client.post("/auth/login", json={"login_id": "", "password": ""})
        assert res.status_code == 422


class TestLockoutRoute:
    def test_다섯_번_틀리면_429(self, client: TestClient) -> None:
        for _ in range(login_attempts.FAILURE_LIMIT):
            assert _login(client, password="wrong").status_code == 401

        res = _login(client)  # 비밀번호가 맞아도
        assert res.status_code == 429

    def test_Retry_After_를_알려_준다(self, client: TestClient) -> None:
        for _ in range(login_attempts.FAILURE_LIMIT):
            _login(client, password="wrong")

        res = _login(client)
        assert 0 < int(res.headers["retry-after"]) <= 600


class TestRefreshRoute:
    def test_쿠키가_바뀐다(self, client: TestClient) -> None:
        _login(client)
        before = client.cookies[REFRESH_COOKIE]

        res = client.post("/auth/refresh")

        assert res.status_code == 200
        assert client.cookies[REFRESH_COOKIE] != before

    def test_쿠키가_없으면_401(self, client: TestClient) -> None:
        assert client.post("/auth/refresh").status_code == 401

    def test_실패하면_쿠키를_지운다(self, client: TestClient) -> None:
        """죽은 토큰을 남겨 두면 다음 요청마다 같은 실패를 반복합니다."""
        _login(client)
        client.cookies.set(REFRESH_COOKIE, "garbage")

        res = client.post("/auth/refresh")

        assert res.status_code == 401
        assert REFRESH_COOKIE not in res.cookies

    def test_재사용이_감지돼도_이유를_알려_주지_않는다(
        self, client: TestClient, store: Store
    ) -> None:
        """공격자에게 "탈취가 들켰다"를 알려 줄 이유가 없습니다."""
        _login(client)
        stolen = client.cookies[REFRESH_COOKIE]
        client.post("/auth/refresh")  # 회전

        row = store.tokens[hash_refresh_token(stolen)]
        row.revoked_at = datetime.now(UTC) - timedelta(minutes=1)
        client.cookies.set(REFRESH_COOKIE, stolen)

        res = client.post("/auth/refresh")

        assert res.status_code == 401
        assert "탈취" not in res.text and "재사용" not in res.text
        assert store.tokens == {}  # 세션은 전부 끊겼습니다


class TestLogoutRoute:
    def test_쿠키를_지운다(self, client: TestClient) -> None:
        _login(client)

        res = client.post("/auth/logout")

        assert res.status_code == 204
        assert not client.cookies.get(REFRESH_COOKIE)

    def test_쿠키가_없어도_204(self, client: TestClient) -> None:
        assert client.post("/auth/logout").status_code == 204

    def test_만료된_access_로도_로그아웃된다(
        self, client: TestClient, store: Store
    ) -> None:
        """access 가 5분이라 만료된 채로 로그아웃하는 일이 흔합니다."""
        _login(client)
        client.cookies.delete(ACCESS_COOKIE)

        assert client.post("/auth/logout").status_code == 204
        assert store.tokens == {}


class TestMeRoute:
    def test_권한_목록을_준다(self, client: TestClient) -> None:
        _login(client)

        body = client.get("/auth/me").json()

        assert body["login_id"] == "daengs"
        assert body["role"] == "ADMIN"
        assert "pii:read" in body["permissions"]

    def test_인증이_없으면_401(self, client: TestClient) -> None:
        assert client.get("/auth/me").status_code == 401

    def test_Bearer_헤더로도_된다(self, client: TestClient) -> None:
        """네이티브 앱은 쿠키 저장소가 없습니다. 토큰은 같은 것을 씁니다."""
        _login(client)
        token = client.cookies[ACCESS_COOKIE]
        client.cookies.clear()

        res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert res.status_code == 200

    def test_정지되면_401(self, client: TestClient, store: Store) -> None:
        """토큰은 살아 있어도 계정이 죽었으면 막아야 합니다."""
        _login(client)
        store.admin.status = "suspended"

        assert client.get("/auth/me").status_code == 401

    def test_비밀번호_해시는_절대_안_나간다(self, client: TestClient) -> None:
        _login(client)

        assert "argon2" not in client.get("/auth/me").text


class TestPermissions:
    def test_권한이_있으면_통과(self, client: TestClient) -> None:
        _login(client)
        assert client.get("/_pii").status_code == 200

    def test_권한이_없으면_403(self, client: TestClient, store: Store) -> None:
        """401 이 아닙니다. 다시 로그인해도 달라지지 않아서,
        401 을 주면 클라이언트가 재발급을 반복하며 무한히 돕니다.
        """
        store.admin.role = "VIEWER"
        _login(client)

        assert client.get("/_pii").status_code == 403

    def test_OPERATOR_는_계정_관리만_못_한다(
        self, client: TestClient, store: Store
    ) -> None:
        store.admin.role = "OPERATOR"
        _login(client)

        assert client.get("/_pii").status_code == 200
        assert client.get("/_manage").status_code == 403

    def test_모르는_role_은_아무것도_못_한다(
        self, client: TestClient, store: Store
    ) -> None:
        """DB 의 CHECK 가 막고 있지만, 뚫렸다면 '권한 없음'으로 떨어져야 합니다."""
        store.admin.role = "SUPERUSER"
        _login(client)

        assert client.get("/_pii").status_code == 403
        assert client.get("/_any").status_code == 200  # 로그인 자체는 유효합니다


class TestRolePermissionMatrix:
    """ROLE_PERMISSIONS 는 03_auth.sql 의 role 주석과 같은 내용이어야 합니다."""

    def test_ADMIN_은_전부_가진다(self) -> None:
        assert Principal(uuid.uuid4(), "ADMIN").permissions == frozenset(Perm)

    def test_OPERATOR_는_계정_관리만_빠진다(self) -> None:
        perms = Principal(uuid.uuid4(), "OPERATOR").permissions
        assert Perm.PII_READ in perms
        assert Perm.ADMIN_MANAGE not in perms

    def test_CURATOR_는_개인정보를_못_본다(self) -> None:
        perms = Principal(uuid.uuid4(), "CURATOR").permissions
        assert Perm.KB_WRITE in perms
        assert Perm.PII_READ not in perms

    def test_ANALYST_는_쓰기가_없다(self) -> None:
        perms = Principal(uuid.uuid4(), "ANALYST").permissions
        assert Perm.METRICS_READ in perms
        assert Perm.OPS_WRITE not in perms
        assert Perm.KB_WRITE not in perms

    def test_VIEWER_는_조회만(self) -> None:
        assert Principal(uuid.uuid4(), "VIEWER").permissions == frozenset({Perm.READ})
