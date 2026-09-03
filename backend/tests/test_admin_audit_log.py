"""관리자 행위 감사 기록 — services/audit.py 와 그 첫 소비자(로그인).

conftest.py 대로 DB 에는 붙지 않습니다. `FakeSession` 이 커밋 경계까지 흉내 내서
(tests/fakes.py) "언제 확정되는가"를 봅니다. 컬럼 타입과 FK 가 맞는지는 여기서 알 수
없고, `db/migrations/verify_2026-09-04_admin_audit_log.sql` 로 실제 DB 에서 봅니다.

**여기서 지키려는 것은 전부 뚫려도 조용한 것들입니다.** 감사 행이 롤백에 쓸려 나가도
로그인은 정상으로 보이고, 화면에도 로그에도 아무 표시가 없습니다 — 나중에 "그때 누가
봤나"를 물었을 때 비로소 없다는 것을 알게 됩니다.
"""

import uuid

import pytest
from fakes import IP, PASSWORD, FakeAdmin, FakeSession, Store, install

from daengs_backend.core.password import hash_password
from daengs_backend.models import (
    AUDIT_ACTIONS,
    AUDIT_LOGIN_DENIED_SUSPENDED,
    AUDIT_LOGIN_FAILED_PASSWORD,
    AUDIT_LOGIN_FAILED_UNKNOWN_ID,
    AUDIT_LOGIN_SUCCESS,
)
from daengs_backend.services import audit, auth, login_attempts


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
def session(store: Store) -> FakeSession:
    """**store 를 넘깁니다** — 이 파일은 커밋 경계를 보는 곳이라 그래야 합니다."""
    return FakeSession(store)


async def _login(session: FakeSession, store: Store, password: str = PASSWORD):
    return await auth.login(
        session,  # type: ignore[arg-type]
        login_id=store.admin.login_id,
        password=password,
        ip=IP,
    )


class TestLoginIsRecorded:
    """네 갈래가 전부 남습니다. 하나라도 빠지면 그 사건만 조용히 사라집니다."""

    async def test_성공(self, session, store) -> None:
        await _login(session, store)

        (entry,) = store.audit_log
        assert entry.action == AUDIT_LOGIN_SUCCESS
        assert entry.admin_user_id == store.admin.id
        assert entry.ip == IP

    async def test_없는_아이디는_주체_없이_남는다(self, session, store) -> None:
        with pytest.raises(auth.InvalidCredentialsError):
            await auth.login(session, login_id="nobody", password=PASSWORD, ip=IP)

        (entry,) = store.audit_log
        assert entry.action == AUDIT_LOGIN_FAILED_UNKNOWN_ID
        # 가리킬 admin_users 행이 없습니다. 단서는 detail 뿐입니다.
        assert entry.admin_user_id is None
        assert entry.detail == {"login_id": "nobody"}

    async def test_틀린_비밀번호(self, session, store) -> None:
        with pytest.raises(auth.InvalidCredentialsError):
            await _login(session, store, password="wrong")

        (entry,) = store.audit_log
        assert entry.action == AUDIT_LOGIN_FAILED_PASSWORD
        assert entry.admin_user_id == store.admin.id

    async def test_정지된_계정은_비밀번호가_맞아도_남는다(self, session, store) -> None:
        store.admin.status = "suspended"

        with pytest.raises(auth.InvalidCredentialsError):
            await _login(session, store)

        (entry,) = store.audit_log
        assert entry.action == AUDIT_LOGIN_DENIED_SUSPENDED
        assert entry.admin_user_id == store.admin.id


class TestCommitBoundary:
    """이 클래스가 이 카드의 회귀 테스트입니다.

    실패 경로는 `InvalidCredentialsError` 로 끝납니다. 감사 행을 세션에 얹기만 하면
    그 예외로 롤백되면서 **에러 하나 없이** 사라집니다. 아래 테스트들은 `audit_log`
    (확정된 것)만 보므로, `record_and_commit` 을 `record` 로 되돌리면 실패합니다.
    """

    @pytest.mark.parametrize(
        ("login_id", "password"),
        [("nobody", PASSWORD), (None, "wrong")],
        ids=["없는_아이디", "틀린_비밀번호"],
    )
    async def test_실패_기록은_예외_전에_확정된다(self, session, store, login_id, password) -> None:
        with pytest.raises(auth.InvalidCredentialsError):
            await auth.login(
                session,
                login_id=login_id or store.admin.login_id,
                password=password,
                ip=IP,
            )

        assert store.audit_log, "예외로 롤백돼 기록이 사라졌습니다"
        assert not store.audit_pending

    async def test_성공은_토큰과_같은_커밋에_들어간다(self, session, store) -> None:
        """성공 경로에서 `record_and_commit` 을 쓰면 커밋이 둘로 갈라집니다.

        그러면 토큰은 나갔는데 기록만 없는(또는 그 반대인) 순간이 생깁니다.
        """
        await _login(session, store)

        assert session.commits == 1

    async def test_커밋하지_않으면_남지_않는다(self, session, store) -> None:
        """가짜가 진짜를 흉내 내고 있는지 자체를 봅니다.

        이게 통과하지 않으면 위 테스트들이 전부 의미가 없습니다 — 무엇을 하든
        `audit_log` 에 쌓이는 가짜였다는 뜻이니까요.
        """
        await audit.record(session, action=AUDIT_LOGIN_SUCCESS)

        assert store.audit_pending
        assert not store.audit_log


class TestWhatWeDoNotStore:
    async def test_비밀번호는_어디에도_없다(self, session, store) -> None:
        with pytest.raises(auth.InvalidCredentialsError):
            await _login(session, store, password="s3cret-guess")

        for entry in store.audit_log:
            assert "s3cret-guess" not in str(entry.detail)
            assert "password" not in (entry.detail or {})

    async def test_시도한_아이디는_50자에서_잘린다(self, session, store) -> None:
        """로그인 칸에는 아무 문자열이나 들어옵니다.

        `admin_users.login_id` 와 같은 길이로 묶어, 감사 테이블이 남의 긴 입력을
        그대로 보관하지 않게 합니다.
        """
        long_id = "a" * 200

        with pytest.raises(auth.InvalidCredentialsError):
            await auth.login(session, login_id=long_id, password=PASSWORD, ip=IP)

        (entry,) = store.audit_log
        assert entry.detail == {"login_id": "a" * 50}


class TestActionVocabulary:
    async def test_쓰는_값이_전부_목록에_있다(self) -> None:
        """`action` 에는 DB CHECK 가 없습니다 (카드마다 늘어나는 목록이라).

        그래서 `AUDIT_ACTIONS` 가 "실제로 무엇이 들어오는가"의 유일한 목록입니다.
        새 행위를 기록하면서 여기 더하는 것을 잊으면, 나중에 화면이 모르는 값을 만납니다.
        """
        used = {
            AUDIT_LOGIN_SUCCESS,
            AUDIT_LOGIN_FAILED_UNKNOWN_ID,
            AUDIT_LOGIN_FAILED_PASSWORD,
            AUDIT_LOGIN_DENIED_SUSPENDED,
        }

        assert used <= set(AUDIT_ACTIONS)

    async def test_대상이_없는_행위는_target_이_비어_있다(self, session, store) -> None:
        """로그인은 "누구에게 한 일"이 아닙니다. 회원 조회(A2)가 생기면 그때 찹니다."""
        await _login(session, store)

        (entry,) = store.audit_log
        assert entry.target_type is None
        assert entry.target_id is None


class TestHelperShape:
    async def test_대상을_넘기면_그대로_남는다(self, session, store) -> None:
        """다음 카드(회원 조회 · 계정 발급)가 쓸 자리를 지금 고정해 둡니다."""
        target = uuid.uuid4()

        await audit.record_and_commit(
            session,
            action="admin.app_user.pii_read",
            admin_user_id=store.admin.id,
            target_type="app_user",
            target_id=target,
            detail={"fields": ["email"]},
            request_id="req-1",
            ip=IP,
        )

        (entry,) = store.audit_log
        assert entry.target_type == "app_user"
        assert entry.target_id == target
        assert entry.detail == {"fields": ["email"]}
        assert entry.request_id == "req-1"
