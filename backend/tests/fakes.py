"""테스트용 메모리 대역.

conftest.py 대로 **DB 에는 붙지 않습니다.** repositories 를 여기 있는 dict 기반
함수로 갈아 끼워서, 서비스와 라우터의 판단만 봅니다.

SQL 이 맞는지는 여기서 알 수 없습니다 — `uv run dev` 로 실제 DB 에 붙여 확인하세요.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from daengs_backend.core.subject import SubjectType
from daengs_backend.repositories import admin_user as admin_user_repo
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import chat as chat_repo
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

    #: 미니룸 이름표. None 이면 아직 안 정한 것입니다.
    room_name: str | None = None
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

        #: 대화 세션·메시지·저장된 요약. 만든 순서대로 담고, 정렬은 가짜
        #: 리포지토리가 진짜와 같은 기준(updated_at 내림차순)으로 합니다.
        self.chat_sessions: list[FakeChatSession] = []
        self.chat_messages: list[FakeChatMessage] = []
        self.chat_summaries: list[FakeChatSummary] = []

        #: `touch_session` 이 부를 때마다 1초씩 앞으로 갑니다. 진짜는 DB 의
        #: `NOW()` 지만, 가짜에서 같은 시각을 주면 "최근 갱신 순"을 볼 수 없습니다.
        self.clock = datetime(2026, 9, 1, tzinfo=UTC)

    def tick(self) -> datetime:
        """다음 시각. 순서를 보는 테스트가 이것에 기댑니다."""
        self.clock += timedelta(seconds=1)
        return self.clock

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
class FakeWalkPet:
    """WalkPet 대역. 그 산책에 나간 아이 하나입니다."""

    pet_id: uuid.UUID


@dataclass
class FakeWalk:
    """Walk 대역. 좌표와 나간 아이들을 리스트로 들고 있습니다 (진짜는 relationship)."""

    app_user_id: uuid.UUID
    client_session_id: uuid.UUID
    started_at: object
    ended_at: object
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    weather_code: int | None = None
    is_day: bool | None = None
    temperature_c: object | None = None
    points: list[FakeWalkPoint] = field(default_factory=list)
    pets: list[FakeWalkPet] = field(default_factory=list)

    @property
    def pet_ids(self) -> list[uuid.UUID]:
        """진짜 모델과 같은 모양. 라우터가 이걸로 응답을 만듭니다."""
        return [link.pet_id for link in self.pets]


@dataclass
class FakeChatSession:
    """ChatSession 대역."""

    app_user_id: uuid.UUID
    pet_id: uuid.UUID
    title: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    agent_categories: list[str] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )


@dataclass
class FakeChatMessage:
    """ChatMessage 대역."""

    session_id: uuid.UUID
    role: str
    content: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    agent_categories: list[str] = field(default_factory=list)
    assistant_status: str | None = None
    request_id: str | None = None
    client_message_id: str | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )


@dataclass
class FakeChatSummary:
    """ChatSummary 대역. **`session_id` 가 nullable 인 것이 요점입니다.**"""

    app_user_id: uuid.UUID
    pet_id: uuid.UUID
    title: str
    question_summary: str
    answer_summary: str
    model: str
    prompt_version: str
    source_message_count: int
    client_request_id: str
    session_id: uuid.UUID | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    key_points: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    source_citations: list[str] = field(default_factory=list)
    agent_categories: list[str] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )


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

    async def pet_owned_ids(session, app_user_id, pet_ids):
        mine = {p.id for p in store.pets if p.app_user_id == app_user_id}
        return mine & set(pet_ids)

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
        # DB 의 walk_pets ON DELETE CASCADE 자리. 그 아이만 연결에서 빠지고
        # 산책 자체는 남습니다.
        for walk in store.walks:
            walk.pets = [link for link in walk.pets if link.pet_id != pet.id]

    monkeypatch.setattr(pet_repo, "list_for_owner", pet_list_for_owner)
    monkeypatch.setattr(pet_repo, "get_owned", pet_get_owned)
    monkeypatch.setattr(pet_repo, "owned_ids", pet_owned_ids)
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

    async def walk_delete_walks_only_with(session, pet_id):
        solo = [
            w
            for w in store.walks
            if w.pets and all(link.pet_id == pet_id for link in w.pets)
        ]
        for walk in solo:
            store.walks.remove(walk)
        return len(solo)

    def walk_add(session, walk):
        if walk.id is None:
            walk.id = uuid.uuid4()
        store.walks.append(walk)
        return walk

    monkeypatch.setattr(walk_repo, "list_for_owner", walk_list_for_owner)
    monkeypatch.setattr(walk_repo, "get_owned", walk_get_owned)
    monkeypatch.setattr(walk_repo, "get_by_client_session", walk_get_by_client_session)
    async def walk_existing_seqs(session, walk_id):
        walk = next((w for w in store.walks if w.id == walk_id), None)
        return {p.client_seq for p in walk.points} if walk else set()

    monkeypatch.setattr(walk_repo, "add", walk_add)
    monkeypatch.setattr(
        walk_repo, "delete_walks_only_with", walk_delete_walks_only_with
    )
    monkeypatch.setattr(walk_repo, "existing_seqs", walk_existing_seqs)

    # -- chats -------------------------------------------------------------
    def _sorted_sessions(app_user_id, pet_id):
        """진짜 리포지토리와 같은 기준 — updated_at 내림차순, 같으면 id 내림차순."""
        mine = [
            s
            for s in store.chat_sessions
            if s.app_user_id == app_user_id and s.pet_id == pet_id
        ]
        return sorted(mine, key=lambda s: (s.updated_at, s.id), reverse=True)

    async def chat_get_owned_pet_id(session, app_user_id, pet_id):
        return next(
            (
                p.id
                for p in store.pets
                if p.id == pet_id and p.app_user_id == app_user_id
            ),
            None,
        )

    async def chat_lock_owned_pet(session, app_user_id, pet_id):
        # 가짜에는 잠글 것이 없습니다. 진짜의 FOR UPDATE 는 동시성용이고,
        # 여기서 보는 것은 **소유권 판정**입니다.
        return await chat_get_owned_pet_id(session, app_user_id, pet_id)

    async def chat_list_sessions(session, app_user_id, pet_id, limit):
        return _sorted_sessions(app_user_id, pet_id)[:limit]

    async def chat_get_owned_session(session, app_user_id, chat_session_id):
        return next(
            (
                s
                for s in store.chat_sessions
                if s.id == chat_session_id and s.app_user_id == app_user_id
            ),
            None,
        )

    async def chat_oldest_sessions_beyond(session, app_user_id, pet_id, keep):
        return _sorted_sessions(app_user_id, pet_id)[keep:]

    def chat_add_session(session, chat_session):
        if chat_session.id is None:
            chat_session.id = uuid.uuid4()
        # 진짜는 DB 의 DEFAULT NOW() 입니다. 만든 순서를 볼 수 있게 시계를 씁니다.
        chat_session.created_at = chat_session.updated_at = store.tick()
        store.chat_sessions.append(chat_session)
        return chat_session

    def chat_touch_session(session, chat_session):
        chat_session.updated_at = store.tick()

    async def chat_delete_session(session, chat_session):
        """**DB 의 FK 동작을 그대로 흉내 냅니다.**

        메시지는 `ON DELETE CASCADE` 로 같이 지워지고, 저장된 요약은
        `ON DELETE SET NULL` 이라 **남고 연결만 끊깁니다.** 이 두 줄이 뒤바뀌면
        사용자가 저장해 둔 요약이 5개 유지에 조용히 사라집니다.

        ⚠️ 여기서 흉내 내는 것이지 SQL 을 검증하는 것은 아닙니다 — 실제 DDL 이
        맞는지는 `test_chat_schema.py` 가 모델과 `db/init/07_chats.sql` 로 봅니다.
        """
        store.chat_sessions.remove(chat_session)
        store.chat_messages = [
            m for m in store.chat_messages if m.session_id != chat_session.id
        ]
        for summary in store.chat_summaries:
            if summary.session_id == chat_session.id:
                summary.session_id = None

    async def chat_list_messages(session, chat_session_id):
        mine = [m for m in store.chat_messages if m.session_id == chat_session_id]
        return sorted(mine, key=lambda m: (m.created_at, m.id))

    async def chat_count_messages(session, chat_session_id):
        return len([m for m in store.chat_messages if m.session_id == chat_session_id])

    async def chat_message_by_idempotency_key(
        session, chat_session_id, client_message_id
    ):
        return next(
            (
                m
                for m in store.chat_messages
                if m.session_id == chat_session_id
                and m.client_message_id == client_message_id
            ),
            None,
        )

    def chat_add_message(session, message):
        if message.id is None:
            message.id = uuid.uuid4()
        message.created_at = store.tick()
        store.chat_messages.append(message)
        return message

    async def chat_list_summaries(session, app_user_id, pet_id):
        # **session_id 로 거르지 않습니다** — 원본이 사라진 요약도 보관함에 남습니다.
        mine = [
            s
            for s in store.chat_summaries
            if s.app_user_id == app_user_id and s.pet_id == pet_id
        ]
        return sorted(mine, key=lambda s: (s.created_at, s.id), reverse=True)

    async def chat_summary_by_idempotency_key(session, app_user_id, client_request_id):
        return next(
            (
                s
                for s in store.chat_summaries
                if s.app_user_id == app_user_id
                and s.client_request_id == client_request_id
            ),
            None,
        )

    def chat_add_summary(session, summary):
        if summary.id is None:
            summary.id = uuid.uuid4()
        summary.created_at = store.tick()
        store.chat_summaries.append(summary)
        return summary

    monkeypatch.setattr(chat_repo, "get_owned_pet_id", chat_get_owned_pet_id)
    monkeypatch.setattr(chat_repo, "lock_owned_pet", chat_lock_owned_pet)
    monkeypatch.setattr(chat_repo, "list_sessions", chat_list_sessions)
    monkeypatch.setattr(chat_repo, "get_owned_session", chat_get_owned_session)
    monkeypatch.setattr(
        chat_repo, "oldest_sessions_beyond", chat_oldest_sessions_beyond
    )
    monkeypatch.setattr(chat_repo, "add_session", chat_add_session)
    monkeypatch.setattr(chat_repo, "touch_session", chat_touch_session)
    monkeypatch.setattr(chat_repo, "delete_session", chat_delete_session)
    monkeypatch.setattr(chat_repo, "list_messages", chat_list_messages)
    monkeypatch.setattr(chat_repo, "count_messages", chat_count_messages)
    monkeypatch.setattr(
        chat_repo, "message_by_idempotency_key", chat_message_by_idempotency_key
    )
    monkeypatch.setattr(chat_repo, "add_message", chat_add_message)
    monkeypatch.setattr(chat_repo, "list_summaries", chat_list_summaries)
    monkeypatch.setattr(
        chat_repo, "summary_by_idempotency_key", chat_summary_by_idempotency_key
    )
    monkeypatch.setattr(chat_repo, "add_summary", chat_add_summary)

    return store
