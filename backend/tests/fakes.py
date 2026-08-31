"""테스트용 메모리 대역.

conftest.py 대로 **DB 에는 붙지 않습니다.** repositories 를 여기 있는 dict 기반
함수로 갈아 끼워서, 서비스와 라우터의 판단만 봅니다.

SQL 이 맞는지는 여기서 알 수 없습니다 — `uv run dev` 로 실제 DB 에 붙여 확인하세요.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from daengs_backend.core.subject import SubjectType
from daengs_backend.repositories import admin_user as admin_user_repo
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import refresh_token as refresh_token_repo
from daengs_backend.repositories import walk as walk_repo

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
class FakeAppUser:
    """AppUser 대역. **`*_enc` 는 진짜 암호문입니다** — 서비스가 core/crypto.py 로
    암호화한 결과가 그대로 들어옵니다. 평문이 새는지 테스트가 볼 수 있어야 합니다.
    """

    kakao_id: int
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email_enc: bytes | None = None
    email_hash: str | None = None
    phone_enc: bytes | None = None
    name_enc: bytes | None = None
    status: str = "active"
    #: 대표 강아지. pets 쪽이 아니라 계정 쪽에 있습니다 (05_pets.sql).
    primary_pet_id: uuid.UUID | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC)
    )


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
        self.rollbacks = 0
        self.flushes = 0

    async def flush(self) -> None:
        """진짜 세션은 여기서 DB 기본값(id)을 받아 옵니다.

        가짜는 셀 뿐입니다 — `FakePet` 이 만들어질 때 id 를 이미 갖고 있어서,
        서비스가 flush 뒤에 id 를 쓰는 흐름이 그대로 돕니다.
        """
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class Store:
    """가짜 저장소의 뒷단. 관리자 한 명, 앱 회원들, refresh 행들을 들고 있습니다."""

    def __init__(self, admin: FakeAdmin) -> None:
        self.admin = admin
        self.tokens: dict[str, FakeToken] = {}
        #: kakao_id → 회원. 앱 회원은 여러 명일 수 있습니다.
        self.app_users: dict[int, FakeAppUser] = {}
        #: 등록 순서대로 담습니다 — 진짜 리포지토리가 created_at 으로 정렬하는 것과
        #: 같은 순서라, "대표를 지우면 먼저 등록한 아이가 승계한다"를 볼 수 있습니다.
        self.pets: list[FakePet] = []

        #: 올라온 산책. 목록은 최근 순이라 진짜 리포지토리가 정렬해서 줍니다.
        self.walks: list[FakeWalk] = []

    def add_app_user(self, user: FakeAppUser) -> FakeAppUser:
        self.app_users[user.kakao_id] = user
        return user


@dataclass
class FakePet:
    """Pet 대역. 암호화 컬럼이 없어 그대로 담습니다 (05_pets.sql 주석)."""

    app_user_id: uuid.UUID
    name: str
    breed: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    sex: str | None = None
    neutered: bool | None = None
    weight_kg: object | None = None
    birth_date: object | None = None
    birth_date_kind: str | None = None


@dataclass
class FakeWalkPoint:
    """WalkPoint 대역."""

    client_seq: int
    chain_index: int
    at: object
    lat: object
    lng: object
    accuracy_m: float | None = None
    is_mock: bool = False


@dataclass
class FakeWalk:
    """Walk 대역. 좌표를 리스트로 들고 있습니다 (진짜는 relationship)."""

    app_user_id: uuid.UUID
    client_session_id: uuid.UUID
    started_at: object
    ended_at: object
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    pet_id: uuid.UUID | None = None
    weather_code: int | None = None
    is_day: bool | None = None
    temperature_c: object | None = None
    points: list[FakeWalkPoint] = field(default_factory=list)


def install(store: Store, monkeypatch: pytest.MonkeyPatch) -> Store:
    """repositories 의 함수들을 store 를 쓰는 것으로 바꿉니다."""

    async def get_by_login_id(session, login_id):
        return store.admin if login_id == store.admin.login_id else None

    async def get_by_id(session, admin_id):
        return store.admin if admin_id == store.admin.id else None

    async def create(session, **kw):
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

    async def get_by_hash(session, token_hash):
        return store.tokens.get(token_hash)

    async def revoke(session, token, at):
        token.revoked_at = at

    async def delete_one(session, token):
        store.tokens.pop(token.token_hash, None)

    async def delete_all_for_subject(session, subject_type, subject_id):
        gone = [
            h
            for h, t in store.tokens.items()
            if t.subject_type == subject_type and t.subject_id == subject_id
        ]
        for h in gone:
            del store.tokens[h]
        return len(gone)

    async def app_get_by_kakao_id(session, kakao_id):
        return store.app_users.get(kakao_id)

    async def app_get_by_id(session, app_user_id):
        for user in store.app_users.values():
            if user.id == app_user_id:
                return user
        return None

    async def app_create(session, **kw):
        # email_hash 의 UNIQUE 를 흉내 냅니다. 진짜 DB 는 IntegrityError 를 내고,
        # 서비스는 그것을 EmailAlreadyRegisteredError 로 바꿉니다.
        email_hash = kw.get("email_hash")
        if email_hash is not None and any(
            u.email_hash == email_hash for u in store.app_users.values()
        ):
            raise IntegrityError("app_users_email_hash_key", None, Exception())
        return store.add_app_user(
            FakeAppUser(
                kakao_id=kw["kakao_id"],
                email_enc=kw.get("email_enc"),
                email_hash=email_hash,
            )
        )

    monkeypatch.setattr(app_user_repo, "get_by_kakao_id", app_get_by_kakao_id)
    monkeypatch.setattr(app_user_repo, "get_by_id", app_get_by_id)
    monkeypatch.setattr(app_user_repo, "create", app_create)

    monkeypatch.setattr(admin_user_repo, "get_by_login_id", get_by_login_id)
    monkeypatch.setattr(admin_user_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(refresh_token_repo, "create", create)
    monkeypatch.setattr(refresh_token_repo, "get_by_hash", get_by_hash)
    monkeypatch.setattr(refresh_token_repo, "revoke", revoke)
    monkeypatch.setattr(refresh_token_repo, "delete_one", delete_one)
    monkeypatch.setattr(
        refresh_token_repo, "delete_all_for_subject", delete_all_for_subject
    )

    # -- pets --------------------------------------------------------------
    async def pet_list_for_owner(session, app_user_id):
        return [p for p in store.pets if p.app_user_id == app_user_id]

    async def pet_get_owned(session, app_user_id, pet_id):
        return next(
            (p for p in store.pets if p.id == pet_id and p.app_user_id == app_user_id),
            None,
        )

    async def pet_count_for_owner(session, app_user_id):
        return len([p for p in store.pets if p.app_user_id == app_user_id])

    def pet_add(session, pet):
        # 진짜 DB 는 `gen_random_uuid()` 로 id 를 채웁니다. 가짜가 그 역할을 합니다 —
        # 안 채우면 서비스가 flush 뒤에 쓰는 `pet.id` 가 None 입니다.
        if pet.id is None:
            pet.id = uuid.uuid4()
        store.pets.append(pet)
        return pet

    async def pet_delete(session, pet):
        store.pets.remove(pet)

    monkeypatch.setattr(pet_repo, "list_for_owner", pet_list_for_owner)
    monkeypatch.setattr(pet_repo, "get_owned", pet_get_owned)
    monkeypatch.setattr(pet_repo, "count_for_owner", pet_count_for_owner)
    monkeypatch.setattr(pet_repo, "add", pet_add)
    monkeypatch.setattr(pet_repo, "delete", pet_delete)

    # -- walks -------------------------------------------------------------
    async def walk_list_for_owner(session, app_user_id):
        mine = [w for w in store.walks if w.app_user_id == app_user_id]
        # 진짜 리포지토리가 started_at 내림차순으로 줍니다.
        return sorted(mine, key=lambda w: w.started_at, reverse=True)

    async def walk_get_owned(session, app_user_id, walk_id):
        return next(
            (
                w
                for w in store.walks
                if w.id == walk_id and w.app_user_id == app_user_id
            ),
            None,
        )

    async def walk_get_by_client_session(
        session, app_user_id, client_session_id
    ):
        return next(
            (
                w
                for w in store.walks
                if w.client_session_id == client_session_id
                and w.app_user_id == app_user_id
            ),
            None,
        )

    def walk_add(session, walk):
        if walk.id is None:
            walk.id = uuid.uuid4()
        store.walks.append(walk)
        return walk

    monkeypatch.setattr(walk_repo, "list_for_owner", walk_list_for_owner)
    monkeypatch.setattr(walk_repo, "get_owned", walk_get_owned)
    monkeypatch.setattr(walk_repo, "get_by_client_session", walk_get_by_client_session)
    monkeypatch.setattr(walk_repo, "add", walk_add)

    return store
