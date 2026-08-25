"""services/auth.py — 로그인 · 회전 · 재사용 감지의 **판단**.

conftest.py 대로 DB 에는 붙지 않습니다. repositories 를 메모리 가짜로 바꿔서
"어떤 상황에 무엇을 결정하는가"만 봅니다. SQL 이 맞는지는 여기서 알 수 없고,
`uv run dev` 로 실제 DB 에 붙여 확인해야 합니다.

여기서 지키려는 것은 대부분 **뚫리면 조용한** 것들입니다 — 잠금 순서가 바뀌어도,
회전이 만료를 연장해도, 로그아웃한 토큰이 유예 창을 타도 화면에는 아무 표시가 없습니다.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from daengs_backend.core.password import hash_password
from daengs_backend.core.token import (
    REFRESH_REUSE_GRACE,
    REFRESH_TTL,
    decode_access_token,
    hash_refresh_token,
)
from daengs_backend.repositories import admin_user as admin_user_repo
from daengs_backend.repositories import refresh_token as refresh_token_repo
from daengs_backend.services import auth, login_attempts

PASSWORD = "correct-horse-battery-staple"
IP = "192.168.0.31"
OTHER_IP = "192.168.0.42"


@dataclass
class FakeAdmin:
    """AdminUser 대역. 서비스가 건드리는 속성만 있습니다."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    login_id: str = "daengs"
    password_hash: str = ""
    role: str = "ADMIN"
    status: str = "active"
    last_login_at: datetime | None = None


@dataclass
class FakeToken:
    """RefreshToken 대역."""

    admin_user_id: uuid.UUID
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None = None
    user_agent: str | None = None
    ip: str | None = None


class FakeSession:
    """commit 만 셉니다. 진짜 쿼리는 아래 가짜 저장소가 가로챕니다."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class Store:
    """가짜 저장소의 뒷단. 관리자 한 명과 refresh 행들을 들고 있습니다."""

    def __init__(self, admin: FakeAdmin) -> None:
        self.admin = admin
        self.tokens: dict[str, FakeToken] = {}


@pytest.fixture
def admin() -> FakeAdmin:
    return FakeAdmin(password_hash=hash_password(PASSWORD))


@pytest.fixture
def store(admin: FakeAdmin, monkeypatch: pytest.MonkeyPatch) -> Store:
    """repositories 를 메모리 dict 로 바꿉니다."""
    st = Store(admin)

    async def get_by_login_id(session, login_id):  # noqa: ANN001, ANN202
        return st.admin if login_id == st.admin.login_id else None

    async def get_by_id(session, admin_id):  # noqa: ANN001, ANN202
        return st.admin if admin_id == st.admin.id else None

    async def create(session, **kw):  # noqa: ANN001, ANN003, ANN202
        token = FakeToken(
            admin_user_id=kw["admin_user_id"],
            token_hash=kw["token_hash"],
            expires_at=kw["expires_at"],
            user_agent=kw.get("user_agent"),
            ip=kw.get("ip"),
        )
        st.tokens[token.token_hash] = token
        return token

    async def get_by_hash(session, token_hash):  # noqa: ANN001, ANN202
        return st.tokens.get(token_hash)

    async def revoke(session, token, at):  # noqa: ANN001, ANN202
        token.revoked_at = at

    async def delete_one(session, token):  # noqa: ANN001, ANN202
        st.tokens.pop(token.token_hash, None)

    async def delete_all_for_admin(session, admin_user_id):  # noqa: ANN001, ANN202
        gone = [h for h, t in st.tokens.items() if t.admin_user_id == admin_user_id]
        for h in gone:
            del st.tokens[h]
        return len(gone)

    monkeypatch.setattr(admin_user_repo, "get_by_login_id", get_by_login_id)
    monkeypatch.setattr(admin_user_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(refresh_token_repo, "create", create)
    monkeypatch.setattr(refresh_token_repo, "get_by_hash", get_by_hash)
    monkeypatch.setattr(refresh_token_repo, "revoke", revoke)
    monkeypatch.setattr(refresh_token_repo, "delete_one", delete_one)
    monkeypatch.setattr(refresh_token_repo, "delete_all_for_admin", delete_all_for_admin)
    return st


@pytest.fixture(autouse=True)
def _clean_attempts() -> None:
    """실패 카운터는 모듈 전역이라 테스트끼리 샙니다."""
    login_attempts.reset_all()


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


async def _login(session: FakeSession, store: Store, password: str = PASSWORD, ip: str = IP):  # noqa: ANN202
    return await auth.login(
        session,  # type: ignore[arg-type]
        login_id=store.admin.login_id,
        password=password,
        ip=ip,
    )


class TestLogin:
    async def test_성공하면_토큰_한_쌍(self, session, store) -> None:  # noqa: ANN001
        pair = await _login(session, store)

        assert decode_access_token(pair.access_token).admin_id == store.admin.id
        assert hash_refresh_token(pair.refresh_token) in store.tokens
        assert store.admin.last_login_at is not None

    async def test_refresh_는_원문이_아니라_해시로_저장된다(self, session, store) -> None:  # noqa: ANN001
        pair = await _login(session, store)

        assert pair.refresh_token not in store.tokens
        assert hash_refresh_token(pair.refresh_token) in store.tokens

    async def test_없는_아이디(self, session, store) -> None:  # noqa: ANN001
        with pytest.raises(auth.InvalidCredentialsError):
            await auth.login(
                session, login_id="nobody", password=PASSWORD, ip=IP
            )

    async def test_틀린_비밀번호(self, session, store) -> None:  # noqa: ANN001
        with pytest.raises(auth.InvalidCredentialsError):
            await _login(session, store, password="wrong")

    async def test_정지된_계정은_같은_예외를_낸다(self, session, store) -> None:  # noqa: ANN001
        """정지 여부가 응답으로 새면 비밀번호를 맞혔다는 사실까지 알려 주게 됩니다."""
        store.admin.status = "suspended"

        with pytest.raises(auth.InvalidCredentialsError):
            await _login(session, store)

    async def test_정지된_계정은_토큰을_남기지_않는다(self, session, store) -> None:  # noqa: ANN001
        store.admin.status = "suspended"

        with pytest.raises(auth.InvalidCredentialsError):
            await _login(session, store)
        assert store.tokens == {}


class TestLockout:
    async def test_다섯_번_틀리면_잠긴다(self, session, store) -> None:  # noqa: ANN001
        for _ in range(login_attempts.FAILURE_LIMIT):
            with pytest.raises(auth.InvalidCredentialsError):
                await _login(session, store, password="wrong")

        # 이제는 비밀번호가 맞아도 들어갈 수 없습니다.
        with pytest.raises(login_attempts.LockedOutError):
            await _login(session, store)

    async def test_없는_아이디로도_잠긴다(self, session, store) -> None:  # noqa: ANN001
        """계정 조회보다 잠금 확인이 먼저여야 합니다.

        순서가 바뀌면 "잠기는 아이디 = 존재하는 아이디"가 되어 계정 목록이 새어 나갑니다.
        """
        for _ in range(login_attempts.FAILURE_LIMIT):
            with pytest.raises(auth.InvalidCredentialsError):
                await auth.login(session, login_id="nobody", password="x", ip=IP)

        with pytest.raises(login_attempts.LockedOutError):
            await auth.login(session, login_id="nobody", password="x", ip=IP)

    async def test_다른_IP_는_잠기지_않는다(self, session, store) -> None:  # noqa: ANN001
        """계정을 팀이 공유하므로, 계정 단위로 잠그면 한 명 때문에 전원이 막힙니다."""
        for _ in range(login_attempts.FAILURE_LIMIT):
            with pytest.raises(auth.InvalidCredentialsError):
                await _login(session, store, password="wrong", ip=IP)

        pair = await _login(session, store, ip=OTHER_IP)
        assert pair.access_token

    async def test_성공하면_카운터가_지워진다(self, session, store) -> None:  # noqa: ANN001
        for _ in range(login_attempts.FAILURE_LIMIT - 1):
            with pytest.raises(auth.InvalidCredentialsError):
                await _login(session, store, password="wrong")

        await _login(session, store)

        # 카운터가 남아 있었다면 한 번 틀리는 순간 잠깁니다.
        with pytest.raises(auth.InvalidCredentialsError):
            await _login(session, store, password="wrong")
        await _login(session, store)


class TestRefresh:
    async def test_회전하면_옛_토큰은_폐기된다(self, session, store) -> None:  # noqa: ANN001
        first = await _login(session, store)

        second = await auth.refresh(session, refresh_token=first.refresh_token)

        assert second.refresh_token != first.refresh_token
        assert store.tokens[hash_refresh_token(first.refresh_token)].revoked_at
        assert store.tokens[hash_refresh_token(second.refresh_token)].revoked_at is None

    async def test_회전해도_만료가_연장되지_않는다(self, session, store) -> None:  # noqa: ANN001
        """절대 만료입니다. 연장하면 매일 쓰는 사람은 영원히 재로그인을 안 합니다."""
        first = await _login(session, store)
        second = await auth.refresh(session, refresh_token=first.refresh_token)

        assert second.refresh_expires_at == first.refresh_expires_at

    async def test_로그인은_REFRESH_TTL_만큼_준다(self, session, store) -> None:  # noqa: ANN001
        before = datetime.now(UTC)
        pair = await _login(session, store)

        drift = abs((pair.refresh_expires_at - (before + REFRESH_TTL)).total_seconds())
        assert drift <= 1

    async def test_모르는_토큰(self, session, store) -> None:  # noqa: ANN001
        with pytest.raises(auth.InvalidRefreshTokenError):
            await auth.refresh(session, refresh_token="wat")

    async def test_만료된_토큰은_탈취로_보지_않는다(self, session, store) -> None:  # noqa: ANN001
        """오래 잠들어 있던 탭 때문에 팀 전체를 로그아웃시키면 안 됩니다."""
        pair = await _login(session, store)
        row = store.tokens[hash_refresh_token(pair.refresh_token)]
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)

        with pytest.raises(auth.InvalidRefreshTokenError):
            await auth.refresh(session, refresh_token=pair.refresh_token)
        # 세션이 남아 있어야 합니다 — 전부 지워졌다면 재사용 감지로 잘못 간 것입니다.
        assert store.tokens

    async def test_정지되면_세션이_전부_정리된다(self, session, store) -> None:  # noqa: ANN001
        """status 는 새 로그인만 막습니다. 이미 나간 세션은 여기서 끊어야 합니다."""
        pair = await _login(session, store)
        store.admin.status = "suspended"

        with pytest.raises(auth.InvalidRefreshTokenError):
            await auth.refresh(session, refresh_token=pair.refresh_token)
        assert store.tokens == {}


class TestReuseDetection:
    async def test_유예_안이면_탭_경합으로_보고_통과시킨다(self, session, store) -> None:  # noqa: ANN001
        """한 브라우저의 탭 두 개가 같은 쿠키로 동시에 재발급하는 상황입니다."""
        first = await _login(session, store)
        await auth.refresh(session, refresh_token=first.refresh_token)

        # 진 쪽이 방금 폐기된 토큰으로 다시 시도합니다.
        third = await auth.refresh(session, refresh_token=first.refresh_token)

        assert third.access_token
        assert store.tokens  # 아무것도 끊기지 않았습니다

    async def test_유예_밖이면_전부_폐기한다(self, session, store) -> None:  # noqa: ANN001
        first = await _login(session, store)
        await auth.refresh(session, refresh_token=first.refresh_token)

        row = store.tokens[hash_refresh_token(first.refresh_token)]
        row.revoked_at = datetime.now(UTC) - (REFRESH_REUSE_GRACE + timedelta(seconds=1))

        with pytest.raises(auth.TokenReuseDetectedError):
            await auth.refresh(session, refresh_token=first.refresh_token)
        assert store.tokens == {}

    async def test_다른_세션까지_같이_끊긴다(self, session, store) -> None:  # noqa: ANN001
        """계정을 공유하므로 팀 전원이 재로그인합니다. 털렸다면 그게 맞습니다."""
        stolen = await _login(session, store)
        teammate = await _login(session, store, ip=OTHER_IP)
        await auth.refresh(session, refresh_token=stolen.refresh_token)

        row = store.tokens[hash_refresh_token(stolen.refresh_token)]
        row.revoked_at = datetime.now(UTC) - (REFRESH_REUSE_GRACE + timedelta(seconds=1))

        with pytest.raises(auth.TokenReuseDetectedError):
            await auth.refresh(session, refresh_token=stolen.refresh_token)
        assert hash_refresh_token(teammate.refresh_token) not in store.tokens


class TestLogout:
    async def test_행을_지운다(self, session, store) -> None:  # noqa: ANN001
        pair = await _login(session, store)

        await auth.logout(session, refresh_token=pair.refresh_token)

        assert hash_refresh_token(pair.refresh_token) not in store.tokens

    async def test_로그아웃한_토큰은_유예_없이_거부된다(self, session, store) -> None:  # noqa: ANN001
        """revoke 가 아니라 delete 인 이유입니다.

        revoked_at 을 찍었다면 유예 창을 타고 10초 동안 통과했을 것입니다.
        """
        pair = await _login(session, store)
        await auth.logout(session, refresh_token=pair.refresh_token)

        with pytest.raises(auth.InvalidRefreshTokenError):
            await auth.refresh(session, refresh_token=pair.refresh_token)

    async def test_없는_토큰이어도_조용히_성공한다(self, session, store) -> None:  # noqa: ANN001
        await auth.logout(session, refresh_token="wat")

    async def test_다른_세션은_남는다(self, session, store) -> None:  # noqa: ANN001
        mine = await _login(session, store)
        teammate = await _login(session, store, ip=OTHER_IP)

        await auth.logout(session, refresh_token=mine.refresh_token)

        assert hash_refresh_token(teammate.refresh_token) in store.tokens
