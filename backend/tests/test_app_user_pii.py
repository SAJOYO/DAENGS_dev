"""회원 원문 복호화와 상태 변경 (A2b · #212).

#211(`test_app_users_admin.py`)이 "가려서 보는 것"을 지켰다면, 여기는 **"원문을 열어
본 것이 기록에 남는가"** 를 지킵니다. 로드맵 §1 의 "누가 복호화를 봤나 — 알 수 없다"를
닫는 자리라, 기록이 안 남으면 이 카드는 아무것도 한 것이 없습니다.

**여기서 지키려는 것 넷:**

  ① 원문을 열면 감사 행이 남는다 — 안 남아도 화면은 멀쩡하다
  ② 그 행에 **값이 안 들어간다** — 들어가면 감사 테이블이 두 번째 개인정보 저장소가 된다
  ③ 기록이 실패하면 원문이 안 나간다 — 순서가 반대면 "본 사람은 있는데 기록은 없는" 조회가 된다
  ④ 탈퇴한 회원의 상태를 관리자가 되돌리지 못한다 — 되돌리면 삭제 요청을 관리자가 무르는 것

`FakeSession` 이 커밋 경계를 흉내 내므로(tests/fakes.py) ①③은 `store.audit_log`(확정된
것)와 `store.audit_pending`(세션에 얹기만 한 것)의 차이로 봅니다.
"""

import uuid

import pytest
from fakes import IP, FakeAdmin, FakeAppUser, FakeSession, FakeToken, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.crypto import encrypt
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Principal, current_admin
from daengs_backend.core.subject import SubjectType
from daengs_backend.models import (
    AUDIT_APP_USER_PII_REVEALED,
    AUDIT_APP_USER_REACTIVATED,
    AUDIT_APP_USER_SUSPENDED,
)
from daengs_backend.routers import app_user_admin as router_module
from daengs_backend.services import app_user_admin as service

EMAIL = "daengs.team@example.com"
PHONE = "010-1234-5678"
NAME = "김유나"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    return install(Store(FakeAdmin()), monkeypatch)


@pytest.fixture
def member(store: Store) -> FakeAppUser:
    return store.add_app_user(
        FakeAppUser(
            kakao_id=200001,
            email_enc=encrypt(EMAIL),
            phone_enc=encrypt(PHONE),
            name_enc=encrypt(NAME),
        )
    )


@pytest.fixture
def session(store: Store) -> FakeSession:
    return FakeSession(store)


class TestReveal:
    async def test_원문이_나온다(self, session, store, member) -> None:
        pii = await service.reveal(  # type: ignore[arg-type]
            session, app_user_id=member.id, actor_id=store.admin.id
        )
        assert pii is not None
        assert (pii.email, pii.phone, pii.name) == (EMAIL, PHONE, NAME)

    async def test_감사_행이_확정된다(self, session, store, member) -> None:
        """`audit_pending` 이 아니라 `audit_log` 여야 합니다 — 커밋까지 됐다는 뜻입니다."""
        await service.reveal(  # type: ignore[arg-type]
            session, app_user_id=member.id, actor_id=store.admin.id, ip=IP
        )

        (entry,) = store.audit_log
        assert entry.action == AUDIT_APP_USER_PII_REVEALED
        assert entry.admin_user_id == store.admin.id
        assert entry.target_type == "app_user"
        assert entry.target_id == member.id
        assert entry.ip == IP
        assert store.audit_pending == []

    async def test_감사에_값이_안_들어간다(self, session, store, member) -> None:
        """**이 카드에서 제일 조용히 뚫리는 자리입니다.**

        값이 섞이면 `admin_audit_log` 가 두 번째 개인정보 저장소가 되고, 탈퇴 시
        파기 대상이 하나 늘어납니다 (`models/admin_audit_log.py` 의 `detail` 주석).
        """
        await service.reveal(  # type: ignore[arg-type]
            session, app_user_id=member.id, actor_id=store.admin.id
        )

        (entry,) = store.audit_log
        # 칸 이름만 남습니다.
        assert entry.detail == {"opened": ["email", "phone", "name"]}
        blob = str(entry.detail)
        for secret in (EMAIL, PHONE, NAME):
            assert secret not in blob

    async def test_연_칸만_남는다(self, session, store) -> None:
        """카카오 동의를 못 받은 칸은 열린 것이 아닙니다."""
        bare = store.add_app_user(
            FakeAppUser(kakao_id=200002, email_enc=encrypt(EMAIL))
        )

        pii = await service.reveal(  # type: ignore[arg-type]
            session, app_user_id=bare.id, actor_id=store.admin.id
        )
        assert pii is not None
        assert (pii.phone, pii.name) == (None, None)
        (entry,) = store.audit_log
        assert entry.detail == {"opened": ["email"]}

    async def test_열_것이_없어도_기록은_남는다(self, session, store) -> None:
        """**시도 자체가 기록 대상입니다.** 결과가 비었다고 안 남기면, "열어 보려 한
        사람"이 사후에 사라집니다."""
        empty = store.add_app_user(FakeAppUser(kakao_id=200003))

        pii = await service.reveal(  # type: ignore[arg-type]
            session, app_user_id=empty.id, actor_id=store.admin.id
        )
        assert pii is not None
        assert pii.opened() == []
        (entry,) = store.audit_log
        assert entry.detail == {"opened": []}

    async def test_기록이_실패하면_원문이_안_나간다(
        self, session, store, member, monkeypatch
    ) -> None:
        """순서가 반대면 "본 사람은 있는데 기록은 없는" 조회가 생깁니다.

        그건 감사 로그가 있는 것이 없는 것보다 나쁜 상태입니다 (`services/audit.py`).
        """
        from daengs_backend.repositories import admin_audit_log as audit_repo

        async def boom(*_a, **_k):
            raise RuntimeError("감사 테이블이 없다")

        monkeypatch.setattr(audit_repo, "add", boom)

        with pytest.raises(RuntimeError):
            await service.reveal(  # type: ignore[arg-type]
                session, app_user_id=member.id, actor_id=store.admin.id
            )

    async def test_없는_회원은_None(self, session, store, member) -> None:
        assert (
            await service.reveal(  # type: ignore[arg-type]
                session, app_user_id=uuid.uuid4(), actor_id=store.admin.id
            )
            is None
        )
        # 없는 회원을 물은 것은 복호화가 아닙니다.
        assert store.audit_log == []


class TestUpdateStatus:
    async def test_정지하면_세션이_끊긴다(self, session, store, member) -> None:
        """`status` 는 새 로그인만 막습니다 (`app_auth.login_with_kakao`)."""
        store.tokens["hash-of-member-session"] = FakeToken(
            subject_type=SubjectType.APP,
            subject_id=member.id,
            token_hash="hash-of-member-session",
            expires_at=store.clock,
        )

        await service.update_status(  # type: ignore[arg-type]
            session, app_user_id=member.id, actor_id=store.admin.id, status="suspended"
        )

        assert member.status == "suspended"
        assert store.tokens == {}
        (entry,) = store.audit_log
        assert entry.action == AUDIT_APP_USER_SUSPENDED
        assert entry.detail == {"sessions_dropped": 1}

    async def test_정지_해제(self, session, store, member) -> None:
        member.status = "suspended"

        await service.update_status(  # type: ignore[arg-type]
            session, app_user_id=member.id, actor_id=store.admin.id, status="active"
        )

        assert member.status == "active"
        (entry,) = store.audit_log
        assert entry.action == AUDIT_APP_USER_REACTIVATED

    async def test_안_바뀌었으면_안_남는다(self, session, store, member) -> None:
        await service.update_status(  # type: ignore[arg-type]
            session, app_user_id=member.id, actor_id=store.admin.id, status="active"
        )
        assert store.audit_log == []

    async def test_탈퇴한_회원은_되돌릴_수_없다(self, session, store, member) -> None:
        """**되돌리면 삭제 요청을 관리자가 무르는 것이 됩니다.**

        되살아나는 길은 하나뿐입니다 — 본인이 카카오로 다시 로그인하면
        `app_auth.login_with_kakao` 가 `active` 로 돌리고 프로필을 다시 채웁니다.
        """
        member.status = "withdrawn"

        with pytest.raises(service.WithdrawnMemberError):
            await service.update_status(  # type: ignore[arg-type]
                session,
                app_user_id=member.id,
                actor_id=store.admin.id,
                status="active",
            )
        assert member.status == "withdrawn"
        assert store.audit_log == []

    async def test_없는_회원(self, session, store) -> None:
        with pytest.raises(service.AppUserNotFoundError):
            await service.update_status(  # type: ignore[arg-type]
                session,
                app_user_id=uuid.uuid4(),
                actor_id=store.admin.id,
                status="suspended",
            )


class TestHttpBoundary:
    @pytest.fixture
    def app(self, store: Store) -> FastAPI:
        test_app = FastAPI()
        test_app.include_router(router_module.router)

        async def _fake_session() -> FakeSession:
            return FakeSession(store)

        test_app.dependency_overrides[get_session] = _fake_session
        return test_app

    @pytest.fixture
    def as_role(self, app: FastAPI, store: Store):
        def _make(role: str) -> TestClient:
            principal = Principal(admin_id=store.admin.id, role=role)

            async def _fake_admin() -> Principal:
                return principal

            app.dependency_overrides[current_admin] = _fake_admin
            return TestClient(app)

        return _make

    def test_원문은_pii_read_만(self, as_role, member) -> None:
        """**이 카드의 진짜 산출물입니다.**

        `PII_READ` 를 가진 것은 ADMIN 과 OPERATOR 뿐입니다 (`core/deps.py` 의
        `ROLE_PERMISSIONS`). #207 이 그 계정을 실제로 만들 수 있게 하기 전까지는
        이 분기가 한 번도 실행되지 않았습니다.
        """
        path = f"/admin/app-users/{member.id}/pii"
        for role in ("ADMIN", "OPERATOR"):
            assert as_role(role).get(path).status_code == 200, role
        for role in ("CURATOR", "ANALYST", "VIEWER"):
            assert as_role(role).get(path).status_code == 403, role

    def test_마스킹_조회는_전원_통과한다(self, as_role, member) -> None:
        """같은 회원의 가려진 값은 `READ` 라 VIEWER 도 봅니다 (#211). 두 문이 다릅니다."""
        for role in ("ADMIN", "OPERATOR", "CURATOR", "ANALYST", "VIEWER"):
            res = as_role(role).get(f"/admin/app-users/{member.id}")
            assert res.status_code == 200, role
            assert EMAIL not in res.text

    def test_정지는_ops_write_다(self, as_role, member) -> None:
        """원문 조회와 **다른 권한**입니다 (`OPS_WRITE` vs `PII_READ`).

        ⚠ **지금 `ROLE_PERMISSIONS` 로는 두 권한이 같은 두 role 을 가립니다** —
        ADMIN 과 OPERATOR 만 둘 다 가지고, CURATOR 의 쓰기는 `KB_WRITE`(지식베이스)
        하나뿐입니다. 그래도 나눠 둔 것은 두 일이 실제로 다르고, role 구성을 바꿀 때
        고칠 자리가 `ROLE_PERMISSIONS` 한 곳이기 때문입니다 (`core/deps.py` 첫 문단).
        화면도 두 버튼을 각각의 권한으로 가립니다.
        """
        path = f"/admin/app-users/{member.id}"
        for role in ("ADMIN", "OPERATOR"):
            assert as_role(role).patch(
                path, json={"status": "suspended"}
            ).status_code == 200, role
        for role in ("CURATOR", "ANALYST", "VIEWER"):
            assert as_role(role).patch(
                path, json={"status": "active"}
            ).status_code == 403, role

    def test_withdrawn_으로는_못_바꾼다(self, as_role, member) -> None:
        """관리자가 status 만 바꿔 만들면 **파기가 안 된 채로 '탈퇴함'인 행**이 생깁니다."""
        res = as_role("ADMIN").patch(
            f"/admin/app-users/{member.id}", json={"status": "withdrawn"}
        )
        assert res.status_code == 422

    def test_탈퇴한_회원은_409(self, as_role, member) -> None:
        member.status = "withdrawn"
        res = as_role("ADMIN").patch(
            f"/admin/app-users/{member.id}", json={"status": "active"}
        )
        assert res.status_code == 409

    def test_상태_변경_응답에_원문이_없다(self, as_role, member) -> None:
        """상태를 바꿨다고 원문을 딸려 보내지 않습니다."""
        body = as_role("ADMIN").patch(
            f"/admin/app-users/{member.id}", json={"status": "suspended"}
        ).text
        for secret in (EMAIL, PHONE, NAME):
            assert secret not in body

    def test_열_것이_없어도_200(self, as_role, store) -> None:
        """"권한이 없다"(403)와 "열 것이 없다"(200 + null)는 다릅니다.

        개발 DB 의 회원 전원이 실제로 이 상태입니다 (카카오 동의 미수신, #211 실측).
        """
        empty = store.add_app_user(FakeAppUser(kakao_id=200009))

        res = as_role("ADMIN").get(f"/admin/app-users/{empty.id}/pii")
        assert res.status_code == 200
        assert res.json() == {"email": None, "phone": None, "name": None}

    def test_없는_회원_원문은_404(self, as_role) -> None:
        res = as_role("ADMIN").get(f"/admin/app-users/{uuid.uuid4()}/pii")
        assert res.status_code == 404
