"""테스트용 메모리 대역.

conftest.py 대로 **DB 에는 붙지 않습니다.** repositories 를 여기 있는 dict 기반
함수로 갈아 끼워서, 서비스와 라우터의 판단만 봅니다.

SQL 이 맞는지는 여기서 알 수 없습니다 — `uv run dev` 로 실제 DB 에 붙여 확인하세요.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest

from daengs_backend.core.subject import SubjectType
from daengs_backend.repositories import admin_user as admin_user_repo
from daengs_backend.repositories import refresh_token as refresh_token_repo

PASSWORD = "correct-horse-battery-staple"
IP = "192.168.0.31"
OTHER_IP = "192.168.0.42"


@dataclass
class FakeAdmin:
    """AdminUser 대역. 서비스가 건드리는 속성만 있습니다."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    login_id: str = "daengs"
    password_hash: str = ""
    name: str = "댕스 관리자"
    role: str = "ADMIN"
    status: str = "active"
    last_login_at: datetime | None = None


@dataclass
class FakeToken:
    """RefreshToken 대역.

    진짜 모델은 소유자 컬럼이 둘이고 그중 하나만 채웁니다 (D-016). 여기서는 한 쌍으로
    들고 있되 **읽는 이름을 모델과 똑같이** 맞춰 둡니다 — 서비스가 `subject_type` /
    `subject_id` 로만 읽기 때문에, 이름이 어긋나면 테스트만 통과하고 실제로는 터집니다.
    """

    subject_type: SubjectType
    subject_id: uuid.UUID
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None = None
    user_agent: str | None = None
    ip: str | None = None


class FakeSession:
    """commit 횟수만 셉니다. 진짜 쿼리는 아래 가짜 저장소가 가로챕니다."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class Store:
    """가짜 저장소의 뒷단. 관리자 한 명과 refresh 행들을 들고 있습니다."""

    def __init__(self, admin: FakeAdmin) -> None:
        self.admin = admin
        self.tokens: dict[str, FakeToken] = {}


def install(store: Store, monkeypatch: pytest.MonkeyPatch) -> Store:
    """repositories 의 함수들을 store 를 쓰는 것으로 바꿉니다."""

    async def get_by_login_id(session, login_id):  # noqa: ANN001, ANN202
        return store.admin if login_id == store.admin.login_id else None

    async def get_by_id(session, admin_id):  # noqa: ANN001, ANN202
        return store.admin if admin_id == store.admin.id else None

    async def create(session, **kw):  # noqa: ANN001, ANN003, ANN202
        token = FakeToken(
            subject_type=kw["subject_type"],
            subject_id=kw["subject_id"],
            token_hash=kw["token_hash"],
            expires_at=kw["expires_at"],
            user_agent=kw.get("user_agent"),
            ip=kw.get("ip"),
        )
        store.tokens[token.token_hash] = token
        return token

    async def get_by_hash(session, token_hash):  # noqa: ANN001, ANN202
        return store.tokens.get(token_hash)

    async def revoke(session, token, at):  # noqa: ANN001, ANN202
        token.revoked_at = at

    async def delete_one(session, token):  # noqa: ANN001, ANN202
        store.tokens.pop(token.token_hash, None)

    async def delete_all_for_subject(session, subject_type, subject_id):  # noqa: ANN001, ANN202
        gone = [
            h
            for h, t in store.tokens.items()
            if t.subject_type == subject_type and t.subject_id == subject_id
        ]
        for h in gone:
            del store.tokens[h]
        return len(gone)

    monkeypatch.setattr(admin_user_repo, "get_by_login_id", get_by_login_id)
    monkeypatch.setattr(admin_user_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(refresh_token_repo, "create", create)
    monkeypatch.setattr(refresh_token_repo, "get_by_hash", get_by_hash)
    monkeypatch.setattr(refresh_token_repo, "revoke", revoke)
    monkeypatch.setattr(refresh_token_repo, "delete_one", delete_one)
    monkeypatch.setattr(
        refresh_token_repo, "delete_all_for_subject", delete_all_for_subject
    )
    return store
