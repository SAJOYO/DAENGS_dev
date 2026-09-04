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
from daengs_backend.repositories import admin_audit_log as admin_audit_log_repo
from daengs_backend.repositories import admin_user as admin_user_repo
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import chat as chat_repo
from daengs_backend.repositories import gait_record as gait_repo
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
class FakeAuditEntry:
    """AdminAuditLog 대역. `admin_user_id` 가 None 인 것은 빠뜨린 게 아니라
    **주체를 특정할 수 없는 행위**입니다 (없는 아이디로 두드린 로그인 실패).
    """

    action: str
    admin_user_id: uuid.UUID | None = None
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    detail: dict | None = None
    request_id: str | None = None
    ip: str | None = None


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
    """commit 횟수만 셉니다. 진짜 쿼리는 아래 가짜 저장소가 가로챕니다.

    `store` 를 넘기면 **감사 행의 커밋 경계까지 흉내 냅니다** — 얹기만 한 행은
    `store.audit_pending` 에 있고 `commit()` 이라야 `store.audit_log` 로 넘어갑니다.
    이것이 없으면 "예외로 롤백돼 기록이 사라지는" 사고를 테스트가 볼 수 없습니다
    (services/audit.py 의 커밋 경계 설명). 안 넘기면 세던 대로만 셉니다.
    """

    def __init__(self, store: "Store | None" = None) -> None:
        self.store = store
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
        if self.store is not None:
            self.store.audit_log.extend(self.store.audit_pending)
            self.store.audit_pending.clear()

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self.store is not None:
            # 커밋 안 된 감사 행은 여기서 사라집니다. 진짜 `get_session` 도
            # 커밋하지 않은 변경을 버리고 닫습니다 (core/database.py).
            self.store.audit_pending.clear()


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
        #: finalize가 저장한 버전된 분석. 진짜 DB의 walk_analyses 자리입니다.
        self.walk_analyses: list[object] = []

        #: 대화 세션·turn·저장된 요약. 정렬은 가짜 리포지토리가 실제 기준을 따릅니다.
        self.chat_sessions: list[FakeChatSession] = []
        self.chat_turns: list[FakeChatTurn] = []
        self.chat_summaries: list[FakeChatSummary] = []

        #: 감사 기록. **둘로 나눈 것이 핵심**입니다 — `audit_pending` 은 세션에
        #: 얹기만 한 것이고, 커밋해야 `audit_log` 로 넘어갑니다 (FakeSession).
        #: 확정을 보고 싶은 테스트는 `audit_log` 만 봐야 합니다.
        self.audit_pending: list[FakeAuditEntry] = []
        self.audit_log: list[FakeAuditEntry] = []

        #: chat completion이 부를 때마다 1초씩 앞으로 갑니다. 진짜는 DB 의
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
    farewell_on: object | None = None


@dataclass
class FakeWalkPointChunk:
    """WalkPointChunk 대역. **좌표 묶음 한 줄**입니다.

    진짜와 같게 `payload` 는 `services/walk_chunk.py` 가 만든 모양이고, 순번과
    개수는 밖에 꺼내 둡니다 — payload 를 풀지 않고 재시도를 판정하기 위해서입니다.
    """

    seq_from: int
    seq_to: int
    point_count: int
    payload: dict


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
    analysis_state: str = "collecting"
    points: list[FakeWalkPointChunk] = field(default_factory=list)
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
    last_message_at: datetime | None = None


@dataclass
class FakeChatTurn:
    """ChatTurn 대역."""

    session_id: uuid.UUID
    client_message_id: uuid.UUID
    processing_status: str
    user_content: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    assistant_content: str | None = None
    agent_categories: list[str] = field(default_factory=list)
    assistant_status: str | None = None
    request_id: str | None = None
    public_response: dict | None = None
    error_code: str | None = None
    processing_started_at: datetime = field(
        default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )
    completed_at: datetime | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )


@dataclass
class FakeChatSummary:
    """ChatSummary reservation 대역."""

    app_user_id: uuid.UUID
    pet_id: uuid.UUID
    source_turn_count: int
    client_request_id: uuid.UUID
    processing_status: str
    source_session_id: uuid.UUID | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str | None = None
    question_summary: str | None = None
    answer_summary: str | None = None
    key_points: list[str] | None = None
    cautions: list[str] | None = None
    source_citations: list[dict] | None = None
    agent_categories: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_version: str | None = None
    error_code: str | None = None
    processing_started_at: datetime = field(
        default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )
    completed_at: datetime | None = None
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

    async def app_get_active_for_update(session, app_user_id):
        user = await app_get_by_id(session, app_user_id)
        return user if user is not None and user.status == "active" else None

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
    monkeypatch.setattr(
        app_user_repo, "get_active_for_update", app_get_active_for_update
    )
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

    async def pet_get_owned(session, app_user_id, pet_id, *, for_update=False):
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

    async def pet_delete_all_for_owner(session, app_user_id):
        owned_ids = {pet.id for pet in store.pets if pet.app_user_id == app_user_id}
        store.pets = [pet for pet in store.pets if pet.app_user_id != app_user_id]
        for walk in store.walks:
            walk.pets = [link for link in walk.pets if link.pet_id not in owned_ids]
        for user in store.app_users.values():
            if user.primary_pet_id in owned_ids:
                user.primary_pet_id = None
        return len(owned_ids)

    monkeypatch.setattr(pet_repo, "list_for_owner", pet_list_for_owner)
    monkeypatch.setattr(
        pet_repo, "list_for_owner_for_update", pet_list_for_owner
    )
    monkeypatch.setattr(pet_repo, "get_owned", pet_get_owned)
    monkeypatch.setattr(pet_repo, "owned_ids", pet_owned_ids)
    monkeypatch.setattr(pet_repo, "count_for_owner", pet_count_for_owner)
    monkeypatch.setattr(pet_repo, "add", pet_add)
    monkeypatch.setattr(pet_repo, "delete", pet_delete)
    monkeypatch.setattr(pet_repo, "delete_all_for_owner", pet_delete_all_for_owner)

    # D-043 gait 행은 별도 focused tests 가 대역을 넣습니다. 일반 pet/auth 테스트에는
    # 보행 기록이 없으므로 빈 잠금 결과를 돌려 storage 설정과 무관하게 둡니다.
    async def gait_list_for_pets_for_update(session, pet_ids):
        return []

    monkeypatch.setattr(
        gait_repo, "list_for_pets_for_update", gait_list_for_pets_for_update
    )

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

    async def walk_get_owned_for_update(session, app_user_id, walk_id):
        return await walk_get_owned(session, app_user_id, walk_id)

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

    async def walk_delete_all_for_owner(session, app_user_id):
        owned = [walk for walk in store.walks if walk.app_user_id == app_user_id]
        store.walks = [walk for walk in store.walks if walk.app_user_id != app_user_id]
        return len(owned)

    def walk_add(session, walk):
        if walk.id is None:
            walk.id = uuid.uuid4()
        if walk.analysis_state is None:
            walk.analysis_state = "collecting"
        store.walks.append(walk)
        return walk

    def walk_add_analysis(session, analysis):
        if analysis.id is None:
            analysis.id = uuid.uuid4()
        if analysis.derived_at is None:
            analysis.derived_at = datetime.now(UTC)
        store.walk_analyses.append(analysis)
        return analysis

    async def walk_get_analysis_for_input(session, **identity):
        return next(
            (
                analysis
                for analysis in store.walk_analyses
                if all(getattr(analysis, key) == value for key, value in identity.items())
            ),
            None,
        )

    monkeypatch.setattr(walk_repo, "list_for_owner", walk_list_for_owner)
    monkeypatch.setattr(walk_repo, "get_owned", walk_get_owned)
    monkeypatch.setattr(walk_repo, "get_owned_for_update", walk_get_owned_for_update)
    monkeypatch.setattr(walk_repo, "get_by_client_session", walk_get_by_client_session)

    async def walk_existing_chunk_starts(session, walk_id):
        walk = next((w for w in store.walks if w.id == walk_id), None)
        return {c.seq_from for c in walk.points} if walk else set()

    monkeypatch.setattr(walk_repo, "add", walk_add)
    monkeypatch.setattr(walk_repo, "add_analysis", walk_add_analysis)
    monkeypatch.setattr(walk_repo, "get_analysis_for_input", walk_get_analysis_for_input)
    monkeypatch.setattr(
        walk_repo, "delete_walks_only_with", walk_delete_walks_only_with
    )
    monkeypatch.setattr(walk_repo, "delete_all_for_owner", walk_delete_all_for_owner)
    monkeypatch.setattr(walk_repo, "existing_chunk_starts", walk_existing_chunk_starts)

    # -- chats -------------------------------------------------------------
    def active_sessions(app_user_id, pet_id):
        mine = [
            row
            for row in store.chat_sessions
            if row.app_user_id == app_user_id
            and row.pet_id == pet_id
            and row.last_message_at is not None
        ]
        return sorted(mine, key=lambda row: (row.last_message_at, row.id), reverse=True)

    async def get_owned_pet_id(session, app_user_id, pet_id):
        return next(
            (p.id for p in store.pets if p.id == pet_id and p.app_user_id == app_user_id),
            None,
        )

    async def get_owned_session(session, app_user_id, session_id):
        return next(
            (s for s in store.chat_sessions if s.id == session_id and s.app_user_id == app_user_id),
            None,
        )

    async def get_draft(session, app_user_id, pet_id):
        return next(
            (
                s
                for s in store.chat_sessions
                if s.app_user_id == app_user_id
                and s.pet_id == pet_id
                and s.last_message_at is None
            ),
            None,
        )

    def add_session(session, row):
        row.id = row.id or uuid.uuid4()
        row.created_at = store.tick()
        store.chat_sessions.append(row)
        return row

    async def delete_session(session, row):
        store.chat_sessions.remove(row)
        store.chat_turns = [turn for turn in store.chat_turns if turn.session_id != row.id]
        for summary in store.chat_summaries:
            if summary.source_session_id == row.id:
                summary.source_session_id = None

    async def list_turns(session, session_id, *, completed_only=False):
        rows = [turn for turn in store.chat_turns if turn.session_id == session_id]
        if completed_only:
            rows = [turn for turn in rows if turn.processing_status == "completed"]
        return sorted(rows, key=lambda row: (row.created_at, row.id))

    async def list_capacity_turns(session, session_id):
        rows = [
            turn
            for turn in store.chat_turns
            if turn.session_id == session_id
            and turn.processing_status in {"processing", "completed"}
        ]
        return sorted(rows, key=lambda row: (row.created_at, row.id))

    async def get_turn_by_client_id(session, session_id, client_message_id):
        return next(
            (
                turn
                for turn in store.chat_turns
                if turn.session_id == session_id
                and turn.client_message_id == client_message_id
            ),
            None,
        )

    async def get_owned_turn(session, app_user_id, turn_id):
        turn = next((turn for turn in store.chat_turns if turn.id == turn_id), None)
        if turn is None:
            return None
        chat_session = await get_owned_session(session, app_user_id, turn.session_id)
        return (turn, chat_session) if chat_session is not None else None

    def add_turn(session, turn):
        turn.id = turn.id or uuid.uuid4()
        turn.created_at = turn.processing_started_at = store.tick()
        store.chat_turns.append(turn)
        return turn

    async def complete_turn(session, turn_id, **values):
        turn = next((row for row in store.chat_turns if row.id == turn_id), None)
        if turn is None or turn.processing_status != "processing":
            return None
        turn.processing_status = "completed"
        for key, value in values.items():
            setattr(turn, key, value)
        turn.completed_at = store.tick()
        return turn

    async def fail_turn(session, turn_id, *, error_code):
        turn = next((row for row in store.chat_turns if row.id == turn_id), None)
        if turn is None or turn.processing_status != "processing":
            return None
        turn.processing_status = "failed"
        turn.error_code = error_code
        turn.completed_at = store.tick()
        return turn

    async def fail_stale_turns(session, *, session_id, cutoff):
        changed = 0
        for turn in store.chat_turns:
            if (
                turn.session_id == session_id
                and turn.processing_status == "processing"
                and turn.processing_started_at < cutoff
            ):
                turn.processing_status = "failed"
                turn.error_code = "STALE_PROCESSING"
                turn.completed_at = store.tick()
                changed += 1
        return changed

    async def prune_failed_turns(session, *, session_id, keep):
        failed = sorted(
            (
                turn
                for turn in store.chat_turns
                if turn.session_id == session_id and turn.processing_status == "failed"
            ),
            key=lambda row: (row.created_at, row.id),
            reverse=True,
        )
        remove_ids = {turn.id for turn in failed[keep:]}
        store.chat_turns = [turn for turn in store.chat_turns if turn.id not in remove_ids]
        return len(remove_ids)

    async def list_completed_summaries(session, app_user_id, pet_id):
        rows = [
            row
            for row in store.chat_summaries
            if row.app_user_id == app_user_id
            and row.pet_id == pet_id
            and row.processing_status == "completed"
        ]
        return sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)

    async def get_owned_summary(session, app_user_id, summary_id):
        return next(
            (
                row
                for row in store.chat_summaries
                if row.id == summary_id and row.app_user_id == app_user_id
            ),
            None,
        )

    async def delete_summary(session, row):
        store.chat_summaries.remove(row)

    async def summary_by_request_id(session, app_user_id, request_id):
        return next(
            (
                row
                for row in store.chat_summaries
                if row.app_user_id == app_user_id and row.client_request_id == request_id
            ),
            None,
        )

    async def active_summary_for_source(session, source_session_id, source_turn_count):
        return next(
            (
                row
                for row in store.chat_summaries
                if row.source_session_id == source_session_id
                and row.source_turn_count == source_turn_count
                and row.processing_status in {"processing", "completed"}
            ),
            None,
        )

    async def fail_stale_summaries(session, *, source_session_id, cutoff):
        changed = 0
        for row in store.chat_summaries:
            if (
                row.source_session_id == source_session_id
                and row.processing_status == "processing"
                and row.processing_started_at < cutoff
            ):
                row.processing_status = "failed"
                row.error_code = "STALE_PROCESSING"
                row.completed_at = store.tick()
                changed += 1
        return changed

    def add_summary(session, row):
        row.id = row.id or uuid.uuid4()
        row.created_at = store.tick()
        store.chat_summaries.append(row)
        return row

    async def complete_summary(session, summary_id, **values):
        row = next((item for item in store.chat_summaries if item.id == summary_id), None)
        if row is None or row.processing_status != "processing":
            return None
        row.processing_status = "completed"
        for key, value in values.items():
            setattr(row, key, value)
        row.completed_at = store.tick()
        return row

    async def fail_summary(session, summary_id, *, error_code):
        row = next((item for item in store.chat_summaries if item.id == summary_id), None)
        if row is None or row.processing_status != "processing":
            return None
        row.processing_status = "failed"
        row.error_code = error_code
        row.completed_at = store.tick()
        return row

    async def delete_all_for_user(session, app_user_id):
        owned_ids = {s.id for s in store.chat_sessions if s.app_user_id == app_user_id}
        store.chat_sessions = [s for s in store.chat_sessions if s.app_user_id != app_user_id]
        store.chat_turns = [turn for turn in store.chat_turns if turn.session_id not in owned_ids]
        store.chat_summaries = [
            summary for summary in store.chat_summaries if summary.app_user_id != app_user_id
        ]

    async def list_active(session, app_user_id, pet_id, limit):
        return active_sessions(app_user_id, pet_id)[:limit]

    async def oldest_active(session, app_user_id, pet_id, keep):
        return active_sessions(app_user_id, pet_id)[keep:]

    def touch_active(session, row, categories):
        row.last_message_at = store.tick()
        row.agent_categories = categories

    monkeypatch.setattr(chat_repo, "get_owned_pet_id", get_owned_pet_id)
    monkeypatch.setattr(chat_repo, "lock_owned_pet", get_owned_pet_id)
    monkeypatch.setattr(chat_repo, "get_owned_session", get_owned_session)
    monkeypatch.setattr(chat_repo, "get_owned_session_for_update", get_owned_session)
    monkeypatch.setattr(chat_repo, "get_draft", get_draft)
    monkeypatch.setattr(chat_repo, "list_active_sessions", list_active)
    monkeypatch.setattr(chat_repo, "oldest_active_sessions_beyond", oldest_active)
    monkeypatch.setattr(chat_repo, "add_session", add_session)
    monkeypatch.setattr(chat_repo, "delete_session", delete_session)
    monkeypatch.setattr(chat_repo, "touch_active_session", touch_active)
    monkeypatch.setattr(chat_repo, "get_turn_by_client_id", get_turn_by_client_id)
    monkeypatch.setattr(chat_repo, "get_owned_turn", get_owned_turn)
    monkeypatch.setattr(chat_repo, "list_capacity_turns", list_capacity_turns)
    monkeypatch.setattr(chat_repo, "list_turns", list_turns)
    monkeypatch.setattr(chat_repo, "add_turn", add_turn)
    monkeypatch.setattr(chat_repo, "complete_turn_if_processing", complete_turn)
    monkeypatch.setattr(chat_repo, "fail_turn_if_processing", fail_turn)
    monkeypatch.setattr(chat_repo, "fail_stale_turns", fail_stale_turns)
    monkeypatch.setattr(chat_repo, "prune_failed_turns", prune_failed_turns)
    monkeypatch.setattr(chat_repo, "get_owned_summary", get_owned_summary)
    monkeypatch.setattr(chat_repo, "delete_summary", delete_summary)
    monkeypatch.setattr(chat_repo, "list_completed_summaries", list_completed_summaries)
    monkeypatch.setattr(chat_repo, "summary_by_request_id", summary_by_request_id)
    monkeypatch.setattr(chat_repo, "active_summary_for_source", active_summary_for_source)
    monkeypatch.setattr(chat_repo, "fail_stale_summaries", fail_stale_summaries)
    monkeypatch.setattr(chat_repo, "add_summary", add_summary)
    monkeypatch.setattr(chat_repo, "complete_summary_if_processing", complete_summary)
    monkeypatch.setattr(chat_repo, "fail_summary_if_processing", fail_summary)
    monkeypatch.setattr(chat_repo, "delete_all_for_user", delete_all_for_user)

    async def audit_add(session, **kw):
        entry = FakeAuditEntry(
            action=kw["action"],
            admin_user_id=kw.get("admin_user_id"),
            target_type=kw.get("target_type"),
            target_id=kw.get("target_id"),
            detail=kw.get("detail"),
            request_id=kw.get("request_id"),
            ip=kw.get("ip"),
        )
        # 얹기만 합니다. 확정은 FakeSession.commit() 이 합니다 — 진짜와 같은 순서라야
        # "커밋 전에 예외가 나면 사라진다"를 테스트가 볼 수 있습니다.
        store.audit_pending.append(entry)
        return entry

    monkeypatch.setattr(admin_audit_log_repo, "add", audit_add)

    return store
