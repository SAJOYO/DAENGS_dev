"""관리자 계정 관리 — services/admin_account.py 와 routers/admin_account.py (A3 · #207).

conftest.py 대로 DB 에는 붙지 않습니다. `FakeSession` 이 커밋 경계를, `Store` 가
`admin_users` 를 대신합니다 (tests/fakes.py). 컬럼 타입과 UNIQUE 제약이 진짜로 맞는지는
여기서 알 수 없고 `uv run dev` 로 실제 DB 에 붙여 봐야 합니다.

**여기서 지키려는 것 셋:**

  ① 스스로를 잠그지 못한다 — 뚫리면 psql 없이는 되돌릴 방법이 없습니다
  ② `password_hash` 가 응답에 안 나간다 — 나가도 화면은 멀쩡해 보입니다
  ③ 계정 변경이 감사 로그에 남는다 — 안 남아도 아무 표시가 없습니다

셋 다 **뚫려도 조용한** 것들입니다. #203 의 감사 로그 테스트가 같은 이유로 있습니다.
"""

import uuid
from typing import Annotated

import pytest
from fakes import IP, FakeAdmin, FakeSession, Store, install
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAdmin, Principal, current_admin
from daengs_backend.core.password import hash_password, verify_password
from daengs_backend.core.subject import SubjectType
from daengs_backend.models import (
    AUDIT_ACCOUNT_CREATED,
    AUDIT_ACCOUNT_REACTIVATED,
    AUDIT_ACCOUNT_ROLE_CHANGED,
    AUDIT_ACCOUNT_SUSPENDED,
)
from daengs_backend.routers import admin_account as admin_account_router
from daengs_backend.services import admin_account as service

NEW_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def owner() -> FakeAdmin:
    """요청을 보내는 ADMIN. 자기 자신 가드의 기준이 되는 사람입니다."""
    return FakeAdmin(login_id="daengs", password_hash=hash_password(NEW_PASSWORD))


@pytest.fixture
def store(owner: FakeAdmin, monkeypatch: pytest.MonkeyPatch) -> Store:
    return install(Store(owner), monkeypatch)


@pytest.fixture
def session(store: Store) -> FakeSession:
    """**store 를 넘깁니다** — 감사 행의 확정을 보려면 커밋 경계가 필요합니다."""
    return FakeSession(store)


class TestCreate:
    async def test_비밀번호는_해시로만_저장된다(self, session, store) -> None:
        created = await service.create_account(
            session,  # type: ignore[arg-type]
            actor_id=store.admin.id,
            login_id="yuna",
            password=NEW_PASSWORD,
            name="김유나",
            role="VIEWER",
        )

        assert created.password_hash != NEW_PASSWORD
        assert created.password_hash.startswith("$argon2id$")
        # 저장한 해시로 실제 로그인이 되는지까지 봅니다. 해시만 확인하면
        # 엉뚱한 값을 해시해도 통과합니다.
        assert verify_password(created.password_hash, NEW_PASSWORD)

    async def test_기본_status_는_active(self, session, store) -> None:
        created = await service.create_account(
            session,  # type: ignore[arg-type]
            actor_id=store.admin.id,
            login_id="yuna",
            password=NEW_PASSWORD,
            name="김유나",
            role="OPERATOR",
        )
        assert created.status == "active"

    async def test_중복_아이디는_LoginIdTakenError(self, session, store) -> None:
        with pytest.raises(service.LoginIdTakenError):
            await service.create_account(
                session,  # type: ignore[arg-type]
                actor_id=store.admin.id,
                login_id=store.admin.login_id,
                password=NEW_PASSWORD,
                name="겹치는 사람",
                role="VIEWER",
            )

    async def test_감사에_남고_비밀번호는_안_남는다(self, session, store) -> None:
        await service.create_account(
            session,  # type: ignore[arg-type]
            actor_id=store.admin.id,
            login_id="yuna",
            password=NEW_PASSWORD,
            name="김유나",
            role="CURATOR",
            ip=IP,
        )

        (entry,) = store.audit_log
        assert entry.action == AUDIT_ACCOUNT_CREATED
        assert entry.admin_user_id == store.admin.id      # 한 사람
        assert entry.target_type == "admin_user"
        assert entry.detail == {"login_id": "yuna", "role": "CURATOR"}
        # detail 은 JSONB 라 무엇이든 들어갑니다. 비밀번호가 섞이면 감사 테이블이
        # 두 번째 비밀 저장소가 됩니다.
        assert NEW_PASSWORD not in str(entry.detail)

    async def test_실패하면_감사에도_안_남는다(self, session, store) -> None:
        """롤백이 계정과 기록을 **같이** 버려야 합니다."""
        with pytest.raises(service.LoginIdTakenError):
            await service.create_account(
                session,  # type: ignore[arg-type]
                actor_id=store.admin.id,
                login_id=store.admin.login_id,
                password=NEW_PASSWORD,
                name="겹치는 사람",
                role="VIEWER",
            )

        assert store.audit_log == []


class TestSelfLockGuard:
    """가드 ① — 자기 자신의 role·status 는 못 바꿉니다."""

    async def test_자기_정지_금지(self, session, store) -> None:
        with pytest.raises(service.SelfChangeError):
            await service.update_account(
                session,  # type: ignore[arg-type]
                actor_id=store.admin.id,
                target_id=store.admin.id,
                status="suspended",
            )
        assert store.admin.status == "active"

    async def test_자기_강등_금지(self, session, store) -> None:
        with pytest.raises(service.SelfChangeError):
            await service.update_account(
                session,  # type: ignore[arg-type]
                actor_id=store.admin.id,
                target_id=store.admin.id,
                role="VIEWER",
            )
        assert store.admin.role == "ADMIN"

    async def test_같은_값이어도_막는다(self, session, store) -> None:
        """"어차피 안 바뀌니까" 로 예외를 두면 그 자리가 자기 승격의 길이 됩니다."""
        with pytest.raises(service.SelfChangeError):
            await service.update_account(
                session,  # type: ignore[arg-type]
                actor_id=store.admin.id,
                target_id=store.admin.id,
                role="ADMIN",
            )


class TestLastAdminGuard:
    """가드 ② — access token 이 최대 5분 낡기 때문에 ①만으로는 부족합니다."""

    async def test_ADMIN_둘이_서로_강등해_0이_되는_길을_막는다(
        self, session, store
    ) -> None:
        # 방금 강등당했지만 아직 ADMIN 토큰을 들고 있는 사람이 있다고 칩니다.
        other = store.add_admin(FakeAdmin(login_id="other", role="ADMIN"))
        await service.update_account(
            session,  # type: ignore[arg-type]
            actor_id=other.id,
            target_id=store.admin.id,
            role="VIEWER",
        )
        assert store.admin.role == "VIEWER"

        # 이제 active ADMIN 은 other 하나뿐입니다. 강등당한 사람이 5분 창 안에서
        # 되받아치면 ADMIN 이 0이 됩니다 — 여기서 막힙니다.
        with pytest.raises(service.LastAdminError):
            await service.update_account(
                session,  # type: ignore[arg-type]
                actor_id=store.admin.id,
                target_id=other.id,
                role="VIEWER",
            )
        assert other.role == "ADMIN"

    async def test_마지막_ADMIN_정지_금지(self, session, store) -> None:
        other = store.add_admin(FakeAdmin(login_id="other", role="OPERATOR"))

        with pytest.raises(service.LastAdminError):
            await service.update_account(
                session,  # type: ignore[arg-type]
                actor_id=other.id,
                target_id=store.admin.id,
                status="suspended",
            )
        assert store.admin.status == "active"

    async def test_ADMIN_이_둘이면_한_명은_내릴_수_있다(self, session, store) -> None:
        other = store.add_admin(FakeAdmin(login_id="other", role="ADMIN"))

        await service.update_account(
            session,  # type: ignore[arg-type]
            actor_id=store.admin.id,
            target_id=other.id,
            role="ANALYST",
        )
        assert other.role == "ANALYST"

    async def test_정지된_ADMIN_은_수에_안_들어간다(self, session, store) -> None:
        """`count_active_with_role` 이 status 를 같이 보는지 확인합니다."""
        store.add_admin(FakeAdmin(login_id="rested", role="ADMIN", status="suspended"))
        other = store.add_admin(FakeAdmin(login_id="other", role="OPERATOR"))

        with pytest.raises(service.LastAdminError):
            await service.update_account(
                session,  # type: ignore[arg-type]
                actor_id=other.id,
                target_id=store.admin.id,
                status="suspended",
            )


class TestSuspend:
    @pytest.fixture
    def target(self, store: Store) -> FakeAdmin:
        return store.add_admin(FakeAdmin(login_id="yuna", role="OPERATOR"))

    async def test_열려_있던_세션이_끊긴다(self, session, store, target) -> None:
        """`status` 는 새 로그인만 막습니다. 이미 나간 세션은 따로 끊어야 합니다."""
        from daengs_backend.repositories import refresh_token as refresh_token_repo

        from datetime import UTC, datetime, timedelta

        await refresh_token_repo.create(
            session,
            subject_type=SubjectType.ADMIN,
            subject_id=target.id,
            token_hash="hash-of-yuna-session",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        assert store.tokens

        await service.update_account(
            session,  # type: ignore[arg-type]
            actor_id=store.admin.id,
            target_id=target.id,
            status="suspended",
        )

        assert store.tokens == {}
        assert target.status == "suspended"

    async def test_정지와_해제가_다른_action_으로_남는다(
        self, session, store, target
    ) -> None:
        await service.update_account(
            session,  # type: ignore[arg-type]
            actor_id=store.admin.id,
            target_id=target.id,
            status="suspended",
        )
        await service.update_account(
            session,  # type: ignore[arg-type]
            actor_id=store.admin.id,
            target_id=target.id,
            status="active",
        )

        actions = [e.action for e in store.audit_log]
        assert actions == [AUDIT_ACCOUNT_SUSPENDED, AUDIT_ACCOUNT_REACTIVATED]

    async def test_role_과_status_를_같이_바꾸면_두_줄(
        self, session, store, target
    ) -> None:
        """한 줄로 합치면 "누가 정지시켰나" 를 세는 것이 파싱이 됩니다."""
        await service.update_account(
            session,  # type: ignore[arg-type]
            actor_id=store.admin.id,
            target_id=target.id,
            role="VIEWER",
            status="suspended",
        )

        actions = [e.action for e in store.audit_log]
        assert actions == [AUDIT_ACCOUNT_ROLE_CHANGED, AUDIT_ACCOUNT_SUSPENDED]

    async def test_안_바뀌었으면_안_남는다(self, session, store, target) -> None:
        """이미 그 값인데 또 부르는 것은 사건이 아닙니다."""
        await service.update_account(
            session,  # type: ignore[arg-type]
            actor_id=store.admin.id,
            target_id=target.id,
            role="OPERATOR",
            status="active",
        )
        assert store.audit_log == []

    async def test_없는_계정(self, session, store) -> None:
        with pytest.raises(service.AdminNotFoundError):
            await service.update_account(
                session,  # type: ignore[arg-type]
                actor_id=store.admin.id,
                target_id=uuid.uuid4(),
                status="suspended",
            )


class TestHttpBoundary:
    """routers/admin_account.py — 상태 코드와 **응답에 무엇이 실리는가**."""

    @pytest.fixture
    def app(self, store: Store) -> FastAPI:
        """계정 라우터만 붙인 앱. main.py 를 안 쓰는 이유는 DB 엔진 때문입니다."""
        test_app = FastAPI()
        test_app.include_router(admin_account_router.router)

        async def _fake_session() -> FakeSession:
            return FakeSession(store)

        test_app.dependency_overrides[get_session] = _fake_session
        return test_app

    @pytest.fixture
    def as_role(self, app: FastAPI, store: Store):
        """그 role 의 관리자로 붙은 클라이언트를 만듭니다.

        토큰을 실제로 굽지 않고 `current_admin` 을 갈아 끼웁니다 — 여기서 보려는 것은
        토큰 해석이 아니라 **권한 게이트**입니다. 토큰 쪽은 test_auth_routes.py 가 봅니다.
        """

        def _make(role: str, admin_id: uuid.UUID | None = None) -> TestClient:
            principal = Principal(admin_id=admin_id or store.admin.id, role=role)

            async def _fake_admin() -> Principal:
                return principal

            app.dependency_overrides[current_admin] = _fake_admin
            return TestClient(app)

        return _make

    def test_ADMIN_만_들어온다(self, as_role) -> None:
        """`ADMIN_MANAGE` 는 ADMIN 만 가집니다 — OPERATOR 도 여기서 막힙니다.

        이 카드 전까지 `ROLE_PERMISSIONS` 의 그 차이는 코드로 한 번도 실행되지
        않았습니다 (`core/deps.py` 의 "자리만 잡아 둔 상태").
        """
        assert as_role("ADMIN").get("/admin/admins").status_code == 200
        for role in ("OPERATOR", "CURATOR", "ANALYST", "VIEWER"):
            assert as_role(role).get("/admin/admins").status_code == 403

    def test_목록에_password_hash_가_없다(self, as_role, store) -> None:
        """나가도 화면은 멀쩡해 보입니다. 스키마가 유일한 방벽입니다."""
        store.admin.password_hash = "$argon2id$v=19$m=65536,t=3,p=4$SECRET"

        res = as_role("ADMIN").get("/admin/admins")

        assert res.status_code == 200
        assert "SECRET" not in res.text
        assert "password" not in res.text
        assert res.json()[0]["login_id"] == store.admin.login_id

    def test_발급은_201_이고_비밀번호를_안_돌려준다(self, as_role) -> None:
        res = as_role("ADMIN").post(
            "/admin/admins",
            json={
                "login_id": "yuna",
                "password": NEW_PASSWORD,
                "name": "김유나",
                "role": "VIEWER",
            },
        )

        assert res.status_code == 201
        assert NEW_PASSWORD not in res.text
        assert res.json()["role"] == "VIEWER"

    def test_중복_아이디는_409(self, as_role, store) -> None:
        res = as_role("ADMIN").post(
            "/admin/admins",
            json={
                "login_id": store.admin.login_id,
                "password": NEW_PASSWORD,
                "name": "겹치는 사람",
                "role": "VIEWER",
            },
        )
        assert res.status_code == 409

    def test_모르는_role_은_422(self, as_role) -> None:
        """`ADMIN_ROLES` 밖의 값은 DB CHECK 까지 가기 전에 막힙니다."""
        res = as_role("ADMIN").post(
            "/admin/admins",
            json={
                "login_id": "yuna",
                "password": NEW_PASSWORD,
                "name": "김유나",
                "role": "SUPERUSER",
            },
        )
        assert res.status_code == 422

    def test_짧은_비밀번호는_422(self, as_role) -> None:
        res = as_role("ADMIN").post(
            "/admin/admins",
            json={
                "login_id": "yuna",
                "password": "short",
                "name": "김유나",
                "role": "VIEWER",
            },
        )
        assert res.status_code == 422

    def test_자기_자신_변경은_403_이_아니라_409(self, as_role, store) -> None:
        """403 이면 프론트가 "권한 없음" 을 띄우고, 다시 로그인하면 될 것 같아 보입니다."""
        res = as_role("ADMIN").patch(
            f"/admin/admins/{store.admin.id}", json={"status": "suspended"}
        )
        assert res.status_code == 409

    def test_없는_계정은_404(self, as_role) -> None:
        res = as_role("ADMIN").patch(
            f"/admin/admins/{uuid.uuid4()}", json={"status": "suspended"}
        )
        assert res.status_code == 404
