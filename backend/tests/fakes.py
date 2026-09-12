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
from daengs_backend.repositories import answer_report as answer_report_repo
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import care_event as care_repo
from daengs_backend.repositories import chat as chat_repo
from daengs_backend.repositories import dogcard as card_repo
from daengs_backend.repositories import gait_record as gait_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import pet_identity as identity_repo
from daengs_backend.repositories import pet_member as pet_member_repo
from daengs_backend.repositories import refresh_token as refresh_token_repo
from daengs_backend.repositories import screening as screening_repo
from daengs_backend.repositories import vet_visit as vet_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.repositories.walk import WalkActivitySums

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
    #: 진짜는 DB DEFAULT NOW() 입니다. 계정 목록 응답(`AdminAccountOut`)이 이 값을
    #: 요구해서 대역에도 둡니다 — 고정값이라 정렬을 보는 데는 못 씁니다.
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )


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
    #: 사람 이름. None 이면 아직 발급 전입니다 (서버가 로그인할 때 채웁니다).
    nickname: str | None = None
    #: OCR 학습 이용 동의 시각. None 이면 미동의입니다 (기본값).
    ocr_consent_at: datetime | None = None
    #: 어느 판에 동의했는지. CHECK `app_users_ocr_consent_pair` 대로 위 칸과
    #: 항상 짝으로 채워지거나 둘 다 None 입니다.
    ocr_consent_version: str | None = None
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


class _FakeSavepoint:
    """`async with session.begin_nested()` 가 성립하게만 합니다."""

    async def __aenter__(self) -> None:
        # 부르는 쪽이 `as` 를 안 씁니다 (`services/app_auth.py`). 진짜 세션은 트랜잭션
        # 객체를 주지만, 안 쓰는 것을 흉내 내면 그것대로 오해를 만듭니다.
        return None

    async def __aexit__(self, *exc: object) -> bool:
        # False 라야 안에서 난 예외가 그대로 바깥으로 나갑니다.
        return False


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
        self.refreshes = 0
        self.savepoints = 0

    async def flush(self) -> None:
        """진짜 세션은 여기서 DB 기본값(id)을 받아 옵니다.

        가짜는 셀 뿐입니다 — `FakePet` 이 만들어질 때 id 를 이미 갖고 있어서,
        서비스가 flush 뒤에 id 를 쓰는 흐름이 그대로 돕니다.
        """
        self.flushes += 1

    def begin_nested(self) -> "_FakeSavepoint":
        """SAVEPOINT 흉내. **아무것도 안 되돌립니다.**

        진짜 세션에서 이것을 쓰는 자리는 닉네임 발급 하나뿐인데
        (`services/app_auth.py` 의 `_ensure_nickname`), 거기서 savepoint 가 막는 것은
        **물어본 뒤 커밋 전에 남이 채가는 경합**입니다. 가짜 저장소에는 동시성이 없어서
        그 경합 자체가 일어나지 않습니다 — 후보를 고르는 판단은 `is_nickname_taken`
        쪽에서 보므로, 여기서는 `async with` 가 성립하기만 하면 됩니다.
        """
        self.savepoints += 1
        return _FakeSavepoint()

    async def refresh(self, obj: object) -> None:
        """진짜 세션은 여기서 서버 기본값(`created_at` 등)을 읽어 옵니다.

        가짜는 아무것도 안 합니다 — `Fake*` 는 만들어질 때 그 값을 이미 갖고 있어서,
        서비스가 refresh 뒤에 그 칸을 읽는 흐름이 그대로 돕니다 (`flush` 와 같은 이유).
        """
        self.refreshes += 1

    async def commit(self) -> None:
        self.commits += 1
        if self.store is not None:
            # 신고의 UNIQUE (turn_id, app_user_id) 는 **여기서** 터집니다. 진짜 DB 와
            # 같은 자리라야 서비스의 `except IntegrityError → rollback` 이 실제로 돕니다.
            for pending in self.store.answer_reports_pending:
                if any(
                    r.turn_id == pending.turn_id
                    and r.app_user_id == pending.app_user_id
                    for r in self.store.answer_reports
                ):
                    raise IntegrityError("duplicate", None, Exception("duplicate"))
            self.store.answer_reports.extend(self.store.answer_reports_pending)
            self.store.answer_reports_pending.clear()

            self.store.audit_log.extend(self.store.audit_pending)
            self.store.audit_pending.clear()

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self.store is not None:
            # 커밋 안 된 감사 행은 여기서 사라집니다. 진짜 `get_session` 도
            # 커밋하지 않은 변경을 버리고 닫습니다 (core/database.py).
            self.store.audit_pending.clear()
            self.store.answer_reports_pending.clear()


class Store:
    """가짜 저장소의 뒷단. 관리자 한 명, 앱 회원들, refresh 행들을 들고 있습니다."""

    def __init__(self, admin: FakeAdmin) -> None:
        self.admin = admin
        #: 관리자 **여러 명**. `admin` 은 그중 첫 번째를 가리키는 이름일 뿐입니다 —
        #: 계정 관리(A3) 이전에는 한 명뿐이라 그 이름만 있었고, 기존 테스트가
        #: 전부 그것을 쓰고 있어 그대로 둡니다. 두 번째부터는 `add_admin`.
        self.admins: list[FakeAdmin] = [admin]
        self.tokens: dict[str, FakeToken] = {}
        #: kakao_id → 회원. 앱 회원은 여러 명일 수 있습니다.
        self.app_users: dict[int, FakeAppUser] = {}
        #: 등록 순서대로 담습니다 — 진짜 리포지토리가 created_at 으로 정렬하는 것과
        #: 같은 순서라, "대표를 지우면 먼저 등록한 아이가 승계한다"를 볼 수 있습니다.
        self.pets: list[FakePet] = []

        #: 공동 돌봄의 돌보미. `(pet_id, app_user_id)` 짝입니다 — 대표는 여기 없고
        #: `FakePet.app_user_id` 가 대표입니다 (docs/co-care.md).
        self.pet_members: list[tuple[uuid.UUID, uuid.UUID]] = []
        #: 초대. `install` 이 만드는 `FakeInvite` 를 담습니다.
        self.pet_invites: list = []
        #: 논리 강아지 그룹 (`FakeIdentity`). 기본은 비어 있습니다 — 연결이 생길 때만
        #: 늘고, 그때까지 모든 `FakePet.identity_id` 는 None 입니다.
        self.pet_identities: list[FakeIdentity] = []

        #: 올라온 산책. 목록은 최근 순이라 진짜 리포지토리가 정렬해서 줍니다.
        self.walks: list[FakeWalk] = []
        #: finalize가 저장한 버전된 분석. 진짜 DB의 walk_analyses 자리입니다.
        self.walk_analyses: list[object] = []

        #: 케어 로그(밥·약·간식) 행 (#332). **기본은 비어 있습니다** — 비서가 `active_dog_id`
        #: 요청마다 오늘 요약을 읽으므로(#344), 여기 대역이 없으면 관련 없는 테스트가
        #: 진짜 리포지토리를 타서 `FakeSession` 에서 죽습니다. 모양은 `test_care_events.py`
        #: 의 `FakeCareEvent` 처럼 `actor_app_user_id · pet_id · kind · occurred_at · id` 면 됩니다.
        self.care_events: list = []

        #: 확정된 진료비 기록 (#353 Task 7). **기본은 비어 있습니다** — 비서가
        #: `active_dog_id` 요청마다 최근 진료비 요약을 읽으므로(`services
        #: .vet_spend_context`), 여기 대역이 없으면 관련 없는 테스트가 진짜
        #: 리포지토리를 타서 `FakeSession` 에서 죽습니다 — `care_events` 와 같은 이유
        #: (바로 위 주석). 모양은 `app_user_id · pet_id · reason_code · visited_on ·
        #: total_krw · hospital_name · hospital_phone · id` 면 됩니다.
        self.vet_visits: list = []

        #: 피부 변화 기록. 사진은 저장소에 있고 여기는 행만 들고 있습니다.
        self.screenings: list = []

        #: 뽑아 둔 도감 카드. id 는 **앱이 만든 것**이라 가짜가 안 채웁니다.
        self.dog_cards: list = []

        #: 대화 세션·turn·저장된 요약. 정렬은 가짜 리포지토리가 실제 기준을 따릅니다.
        self.chat_sessions: list[FakeChatSession] = []
        self.chat_turns: list[FakeChatTurn] = []
        self.chat_summaries: list[FakeChatSummary] = []

        #: AI 답변 신고 (A1 · D-053). 진짜는 turn_id FK 가 CASCADE 라 대화가 지워지면
        #: 같이 사라지는데, 가짜는 그 배선을 흉내 내지 않습니다 — 그 동작은 SQL 의
        #: 몫이라 verify 스크립트로 지킵니다.
        self.answer_reports: list[FakeAnswerReport] = []
        #: 아직 커밋 안 된 신고. 감사 행과 같은 이유로 나눠 둡니다 — **중복은
        #: commit 에서 터져야** 서비스의 `except IntegrityError` 경로를 테스트가
        #: 실제로 지나갑니다 (진짜 `session.add()` 는 예외를 내지 않습니다).
        self.answer_reports_pending: list[FakeAnswerReport] = []

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

    def add_admin(self, admin: FakeAdmin) -> FakeAdmin:
        """관리자를 한 명 더. 계정 관리 테스트가 씁니다."""
        self.admins.append(admin)
        return admin


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
    updated_at: object | None = None

    #: 논리 강아지 그룹 (MVP 결정 §3). **None 이면 연결 안 된 보통 강아지**입니다 —
    #: 기존 테스트가 이 칸을 모르고도 그대로 도는 이유입니다.
    identity_id: uuid.UUID | None = None

    # 돌봄 (#331). None 은 '모름'입니다.
    feeding_style: str | None = None
    feeding_times: list[str] | None = None
    health_conditions: str | None = None
    medications: str | None = None

    # 프로필 사진 (D-052). 사진 자체는 저장소에 있고 여기는 그 자리만 적습니다.
    photo_storage_key: str | None = None
    photo_content_type: str | None = None
    photo_generation: str | None = None
    photo_size_bytes: int | None = None
    photo_updated_at: object | None = None
    photo_pending_key: str | None = None
    photo_pending_content_type: str | None = None
    photo_pending_at: object | None = None


@dataclass
class FakeIdentity:
    """PetIdentity 대역. 여러 `FakePet` 이 같은 실제 강아지임을 나타냅니다.

    **앵커(`owner_pet_id`)가 그룹 주보호자입니다** — 사람 id 를 따로 안 들고 있는 것이
    진짜와 같습니다 (`models/pet_identity.py`).
    """

    owner_pet_id: uuid.UUID
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class FakeInvite:
    """PetInvite 대역. 공동 돌봄 초대 (docs/co-care.md §3)."""

    pet_id: uuid.UUID
    invited_by: uuid.UUID
    token_hash: str
    expires_at: datetime
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime(2026, 9, 9, tzinfo=UTC))
    #: 영수증(2026-09-10, #388·#261). `None` 이면 아직 안 쓴 초대입니다.
    accepted_at: datetime | None = None
    accepted_by: uuid.UUID | None = None


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


@dataclass
class FakeAnswerReport:
    """AnswerReport 대역. **답변 원문이 없습니다** — turn_id 가 그것을 가리킵니다."""

    turn_id: uuid.UUID
    app_user_id: uuid.UUID
    reason: str
    status: str = "open"
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    reviewed_by: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )


def install(store: Store, monkeypatch: pytest.MonkeyPatch) -> Store:
    """repositories 의 함수들을 store 를 쓰는 것으로 바꿉니다."""

    async def get_by_login_id(session, login_id):
        return next((a for a in store.admins if a.login_id == login_id), None)

    async def get_by_id(session, admin_id):
        return next((a for a in store.admins if a.id == admin_id), None)

    async def admin_list_all(session):
        return sorted(store.admins, key=lambda a: a.login_id)

    async def admin_create(session, **kw):
        # `admin_users_login_id_key` UNIQUE 를 흉내 냅니다. 진짜 DB 는 flush 에서
        # IntegrityError 를 내고, 서비스가 그것을 LoginIdTakenError 로 바꿉니다.
        if any(a.login_id == kw["login_id"] for a in store.admins):
            raise IntegrityError("admin_users_login_id_key", None, Exception())
        return store.add_admin(
            FakeAdmin(
                login_id=kw["login_id"],
                password_hash=kw["password_hash"],
                name=kw["name"],
                role=kw["role"],
            )
        )

    async def admin_count_active_with_role(session, role):
        return sum(1 for a in store.admins if a.role == role and a.status == "active")

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

    async def app_get_by_email_hash(session, email_hash):
        # 진짜와 같이 **`status` 로 거르지 않습니다.** 다만 탈퇴한 회원은 해시 자체가
        # 지워져 있어(services/app_auth.py) 애초에 안 걸립니다.
        return next(
            (u for u in store.app_users.values() if u.email_hash == email_hash), None
        )

    async def app_get_active_for_update(session, app_user_id):
        user = await app_get_by_id(session, app_user_id)
        return user if user is not None and user.status == "active" else None

    async def app_nicknames_by_ids(session, app_user_ids):
        wanted = set(app_user_ids)
        return {u.id: u.nickname for u in store.app_users.values() if u.id in wanted}

    async def app_is_nickname_taken(session, nickname):
        # 진짜와 같이 **소문자로 접어서** 봅니다 (lower(nickname) UNIQUE 인덱스).
        folded = nickname.lower()
        return any(
            u.nickname is not None and u.nickname.lower() == folded
            for u in store.app_users.values()
        )

    async def app_search_by_nickname(session, term, *, limit=20):
        needle = term.lower()
        found = [
            u
            for u in store.app_users.values()
            if u.nickname is not None and needle in u.nickname.lower()
        ]
        return found[:limit]

    async def app_list_page(session, *, limit, before=None):
        # 진짜와 같이 **status 로 거르지 않습니다** — 정지·탈퇴도 목록에 나옵니다.
        # 정렬도 같습니다: 가입 최근 순이고, 같은 시각이면 id 로 한 번 더 가릅니다.
        rows = sorted(
            store.app_users.values(),
            key=lambda u: (u.created_at, u.id),
            reverse=True,
        )
        if before is not None:
            # 진짜 쿼리의 튜플 비교 `(created_at, id) < (at, id)` 자리입니다.
            rows = [u for u in rows if (u.created_at, u.id) < before]
        return rows[:limit]

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
    monkeypatch.setattr(app_user_repo, "get_by_email_hash", app_get_by_email_hash)
    monkeypatch.setattr(
        app_user_repo, "get_active_for_update", app_get_active_for_update
    )
    monkeypatch.setattr(app_user_repo, "create", app_create)
    monkeypatch.setattr(app_user_repo, "is_nickname_taken", app_is_nickname_taken)
    monkeypatch.setattr(app_user_repo, "search_by_nickname", app_search_by_nickname)
    monkeypatch.setattr(app_user_repo, "list_page", app_list_page)
    monkeypatch.setattr(app_user_repo, "nicknames_by_ids", app_nicknames_by_ids)

    monkeypatch.setattr(admin_user_repo, "get_by_login_id", get_by_login_id)
    monkeypatch.setattr(admin_user_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(admin_user_repo, "list_all", admin_list_all)
    monkeypatch.setattr(admin_user_repo, "create", admin_create)
    monkeypatch.setattr(
        admin_user_repo, "count_active_with_role", admin_count_active_with_role
    )
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

    def _member_pet_ids(user_id):
        return {pid for pid, uid in store.pet_members if uid == user_id}

    async def pet_get_accessible(session, app_user_id, pet_id, *, for_update=False):
        ids = _member_pet_ids(app_user_id)
        return next(
            (
                p
                for p in store.pets
                if p.id == pet_id and (p.app_user_id == app_user_id or p.id in ids)
            ),
            None,
        )

    async def pet_count_accessible(session, app_user_id):
        # 진짜와 같게 **논리 강아지**를 셉니다 (MVP 결정 §4) —
        # `COUNT(DISTINCT COALESCE(identity_id, id))`. 연결이 없으면 행 수와 같습니다.
        ids = _member_pet_ids(app_user_id)
        keys = {
            p.identity_id or p.id
            for p in store.pets
            if p.app_user_id == app_user_id or p.id in ids
        }
        return len(keys)

    async def pet_by_ids(session, pet_ids):
        wanted = set(pet_ids)
        return {p.id: p for p in store.pets if p.id in wanted}

    async def pet_get_many_for_update(session, pet_ids):
        # 진짜는 `ORDER BY id` 로 잠급니다. 가짜에는 동시성이 없어 잠금은 흉내 내지 않고,
        # "여러 행을 id 로 한 번에 찾는다" 는 뜻만 지킵니다.
        wanted = set(pet_ids)
        return {p.id: p for p in store.pets if p.id in wanted}

    async def pet_get_by_id_for_update(session, pet_id):
        # 진짜와 같게 **소유자 조건이 없습니다** — 초대 수락처럼 권한 판단 전에
        # 행을 잠그기만 하는 경로가 씁니다. 가짜에는 동시성이 없어 락 자체는 흉내
        # 내지 않고, "누구 것이든 id 로 찾는다" 는 뜻만 지킵니다.
        return next((p for p in store.pets if p.id == pet_id), None)

    async def pet_find_by_photo_key(session, storage_key, *, pending):
        # 진짜와 같게 **소유자 조건이 없습니다** — bridge 는 인증 헤더를 안 받고
        # "backend 가 발급한 키인가" 만 봅니다.
        attr = "photo_pending_key" if pending else "photo_storage_key"
        return next((p for p in store.pets if getattr(p, attr) == storage_key), None)

    async def pet_owned_ids(session, app_user_id, pet_ids):
        mine = {p.id for p in store.pets if p.app_user_id == app_user_id}
        return mine & set(pet_ids)

    async def pet_accessible_ids(session, app_user_id, pet_ids):
        # 진짜와 같게 **구성원(대표 ∪ 돌보미)** 입니다 (docs/co-care.md §2).
        ids = _member_pet_ids(app_user_id)
        mine = {
            p.id for p in store.pets if p.app_user_id == app_user_id or p.id in ids
        }
        return mine & set(pet_ids)

    async def pet_list_accessible(session, app_user_id):
        ids = _member_pet_ids(app_user_id)
        # 진짜는 `created_at, id` 로 정렬합니다. 대역의 `store.pets` 는 등록 순서라
        # 그 순서가 곧 같은 뜻입니다 (`pet_list_for_owner` 와 같은 규칙).
        return [
            p for p in store.pets if p.app_user_id == app_user_id or p.id in ids
        ]

    async def pet_count_for_owner(session, app_user_id):
        return len([p for p in store.pets if p.app_user_id == app_user_id])

    async def pet_names_by_ids(session, pet_ids):
        wanted = set(pet_ids)
        return {p.id: p.name for p in store.pets if p.id in wanted}

    async def pet_owners_by_ids(session, pet_ids):
        # gait·screening 의 can_confirm/can_delete/created_by 가 한 번에 쓰는 대표 맵
        # (Task 19, docs/co-care.md §2). `pet_names_by_ids` 와 같은 모양.
        wanted = set(pet_ids)
        return {p.id: p.app_user_id for p in store.pets if p.id in wanted}

    async def pet_count_by_owners(session, app_user_ids):
        # 진짜와 같이 **한 마리도 없는 주인은 키가 아예 없습니다** (GROUP BY 가 행을
        # 안 만듭니다). 부르는 쪽이 .get(id, 0) 을 안 쓰면 여기서 걸립니다.
        wanted = set(app_user_ids)
        counts: dict = {}
        for pet in store.pets:
            if pet.app_user_id in wanted:
                counts[pet.app_user_id] = counts.get(pet.app_user_id, 0) + 1
        return counts

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
        # `pet_members.pet_id` 의 CASCADE 자리 — 강아지가 사라지면 돌보미 행도 같이
        # 사라집니다. **여기서는 실제로 돕니다** (탈퇴와 달리 행이 진짜로 지워집니다).
        store.pet_members = [row for row in store.pet_members if row[0] != pet.id]
        # `app_users.primary_pet_id` 의 ON DELETE SET NULL 자리. 대표든 돌보미든
        # 가리키고 있던 사람은 전부 NULL 이 됩니다 — **누구를 대신 세울지는 서비스가
        # 정합니다.** 이 대역이 없으면 "지운 뒤에 확인하는" 잘못된 구현이 통과합니다.
        for user in store.app_users.values():
            if user.primary_pet_id == pet.id:
                user.primary_pet_id = None
        _cascade_identity_anchor({pet.id})

    def _cascade_identity_anchor(deleted_pet_ids):
        """`pet_identities.owner_pet_id` 의 ON DELETE CASCADE + `pets.identity_id` 의
        SET NULL 자리.

        앵커 행이 지워지면 그룹 행이 사라지고, 그 그룹을 가리키던 **남은 pet 행은
        독립 강아지로 되돌아갑니다** — 행도 기록도 안 지워지는 것이 논리 연결의 전제라
        이 대역이 없으면 "그룹은 없는데 identity_id 는 남은" 상태를 테스트가 못 잡습니다.
        """
        orphaned = [
            identity
            for identity in store.pet_identities
            if identity.owner_pet_id in deleted_pet_ids
        ]
        if not orphaned:
            return
        gone = {identity.id for identity in orphaned}
        store.pet_identities = [
            identity for identity in store.pet_identities if identity.id not in gone
        ]
        for pet in store.pets:
            if pet.identity_id in gone:
                pet.identity_id = None

    async def pet_delete_all_for_owner(session, app_user_id):
        owned_ids = {pet.id for pet in store.pets if pet.app_user_id == app_user_id}
        store.pets = [pet for pet in store.pets if pet.app_user_id != app_user_id]
        for walk in store.walks:
            walk.pets = [link for link in walk.pets if link.pet_id not in owned_ids]
        for user in store.app_users.values():
            if user.primary_pet_id in owned_ids:
                user.primary_pet_id = None
        store.pet_members = [row for row in store.pet_members if row[0] not in owned_ids]
        _cascade_identity_anchor(owned_ids)
        return len(owned_ids)

    # -- pet_identities (논리 강아지) ---------------------------------------
    def identity_add(session, owner_pet_id):
        identity = FakeIdentity(owner_pet_id=owner_pet_id)
        store.pet_identities.append(identity)
        return identity

    async def identity_get_many(session, identity_ids):
        wanted = set(identity_ids)
        return {i.id: i for i in store.pet_identities if i.id in wanted}

    async def identity_pets_for(session, identity_id):
        return [p for p in store.pets if p.identity_id == identity_id]

    async def identity_pet_ids_for(session, identity_id):
        return [p.id for p in store.pets if p.identity_id == identity_id]

    async def identity_count_pets(session, identity_id):
        return sum(1 for p in store.pets if p.identity_id == identity_id)

    async def identity_guardian_ids(session, identity_id):
        # 진짜와 같게 **중복 제거된 사용자 집합**입니다 (대표 ∪ 돌보미). 행별로 세면
        # 연결할 때마다 그룹 인원이 상한을 넘어 늘어납니다 (MVP 결정 §4).
        group = {p.id for p in store.pets if p.identity_id == identity_id}
        owners = {p.app_user_id for p in store.pets if p.id in group}
        carers = {uid for pid, uid in store.pet_members if pid in group}
        return owners | carers

    async def identity_delete(session, identity_id):
        before = len(store.pet_identities)
        store.pet_identities = [
            i for i in store.pet_identities if i.id != identity_id
        ]
        return before - len(store.pet_identities)

    monkeypatch.setattr(identity_repo, "add", identity_add)
    monkeypatch.setattr(identity_repo, "get_many", identity_get_many)
    monkeypatch.setattr(identity_repo, "pets_for", identity_pets_for)
    monkeypatch.setattr(identity_repo, "pet_ids_for", identity_pet_ids_for)
    monkeypatch.setattr(identity_repo, "count_pets", identity_count_pets)
    monkeypatch.setattr(identity_repo, "guardian_ids", identity_guardian_ids)
    monkeypatch.setattr(identity_repo, "delete", identity_delete)

    monkeypatch.setattr(pet_repo, "list_for_owner", pet_list_for_owner)
    monkeypatch.setattr(
        pet_repo, "list_for_owner_for_update", pet_list_for_owner
    )
    monkeypatch.setattr(pet_repo, "get_owned", pet_get_owned)
    monkeypatch.setattr(pet_repo, "get_accessible", pet_get_accessible)
    monkeypatch.setattr(pet_repo, "count_accessible", pet_count_accessible)
    monkeypatch.setattr(pet_repo, "by_ids", pet_by_ids)
    monkeypatch.setattr(pet_repo, "get_many_for_update", pet_get_many_for_update)
    monkeypatch.setattr(pet_repo, "get_by_id_for_update", pet_get_by_id_for_update)
    monkeypatch.setattr(pet_repo, "find_by_photo_key", pet_find_by_photo_key)
    monkeypatch.setattr(pet_repo, "owned_ids", pet_owned_ids)
    monkeypatch.setattr(pet_repo, "accessible_ids", pet_accessible_ids)
    monkeypatch.setattr(pet_repo, "list_accessible", pet_list_accessible)
    monkeypatch.setattr(pet_repo, "count_for_owner", pet_count_for_owner)
    monkeypatch.setattr(pet_repo, "count_by_owners", pet_count_by_owners)
    monkeypatch.setattr(pet_repo, "names_by_ids", pet_names_by_ids)
    monkeypatch.setattr(pet_repo, "owners_by_ids", pet_owners_by_ids)
    monkeypatch.setattr(pet_repo, "add", pet_add)
    monkeypatch.setattr(pet_repo, "delete", pet_delete)
    monkeypatch.setattr(pet_repo, "delete_all_for_owner", pet_delete_all_for_owner)

    # -- pet_members / pet_invites (공동 돌봄, docs/co-care.md §3) ---------
    async def member_list_members(session, pet_id):
        return [uid for pid, uid in store.pet_members if pid == pet_id]

    async def member_is_member(session, pet_id, app_user_id):
        # 진짜와 같게 **대표도 True 입니다** — 구성원은 대표 ∪ 돌보미입니다.
        owner = next((p.app_user_id for p in store.pets if p.id == pet_id), None)
        if owner == app_user_id:
            return True
        return (pet_id, app_user_id) in store.pet_members

    async def member_members_in(session, pairs):
        # `member_is_member` 의 목록판 — **대표 여부는 안 봅니다**(진짜와 같습니다,
        # `pet_owners_by_ids` 가 따로 압니다). `actor_labels` 가 씁니다 (Task 19).
        wanted = set(pairs)
        return {row for row in store.pet_members if row in wanted}

    async def member_count_members(session, pet_id):
        # 진짜와 같게 **대표를 포함해서** 셉니다.
        return sum(1 for pid, _ in store.pet_members if pid == pet_id) + 1

    def member_add(session, pet_id, app_user_id):
        # `pet_members` 의 PK `(pet_id, app_user_id)` 를 흉내 냅니다. 진짜 DB 는 flush 에서
        # IntegrityError 를 냅니다 — `admin_create` 의 `admin_users_login_id_key` 대역과
        # 같은 요령입니다. 이게 없으면 `accept_invite` 의 `is_member()` 조기 반환이
        # 지워져도 이 자리가 조용히 중복 행을 쌓아 테스트가 그 회귀를 못 잡습니다.
        if (pet_id, app_user_id) in store.pet_members:
            raise IntegrityError("pet_members_pkey", None, Exception())
        store.pet_members.append((pet_id, app_user_id))
        return (pet_id, app_user_id)

    async def member_remove(session, pet_id, app_user_id):
        before = len(store.pet_members)
        store.pet_members = [
            row for row in store.pet_members if row != (pet_id, app_user_id)
        ]
        return before - len(store.pet_members)

    async def member_get_invite_by_hash(session, token_hash):
        return next(
            (i for i in store.pet_invites if i.token_hash == token_hash), None
        )

    async def member_count_valid_invites(session, pet_id, now):
        # 수락된(영수증) 행은 뺍니다 — repositories/pet_member.py 의 진짜 쿼리와 같은 규칙.
        return sum(
            1
            for i in store.pet_invites
            if i.pet_id == pet_id and i.expires_at > now and i.accepted_by is None
        )

    def member_add_invite(session, *, pet_id, invited_by, token_hash, expires_at):
        invite = FakeInvite(
            pet_id=pet_id,
            invited_by=invited_by,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        store.pet_invites.append(invite)
        return invite

    async def member_delete_invite(session, invite_id):
        before = len(store.pet_invites)
        store.pet_invites = [i for i in store.pet_invites if i.id != invite_id]
        return before - len(store.pet_invites)

    async def member_delete_expired_invites(session, pet_id, now):
        before = len(store.pet_invites)
        store.pet_invites = [
            i
            for i in store.pet_invites
            if not (i.pet_id == pet_id and i.expires_at <= now)
        ]
        return before - len(store.pet_invites)

    async def member_delete_invites_for_pet(session, pet_id):
        before = len(store.pet_invites)
        store.pet_invites = [i for i in store.pet_invites if i.pet_id != pet_id]
        return before - len(store.pet_invites)

    async def member_delete_invite_for_pet(session, pet_id, invite_id):
        before = len(store.pet_invites)
        store.pet_invites = [
            i for i in store.pet_invites if not (i.id == invite_id and i.pet_id == pet_id)
        ]
        return before - len(store.pet_invites)

    async def member_list_invites(session, pet_id):
        return sorted(
            (i for i in store.pet_invites if i.pet_id == pet_id),
            key=lambda i: (i.created_at, i.id),
        )

    monkeypatch.setattr(pet_member_repo, "list_members", member_list_members)
    monkeypatch.setattr(pet_member_repo, "is_member", member_is_member)
    monkeypatch.setattr(pet_member_repo, "members_in", member_members_in)
    monkeypatch.setattr(pet_member_repo, "count_members", member_count_members)
    monkeypatch.setattr(pet_member_repo, "add", member_add)
    monkeypatch.setattr(pet_member_repo, "remove", member_remove)
    monkeypatch.setattr(
        pet_member_repo, "get_invite_by_hash", member_get_invite_by_hash
    )
    monkeypatch.setattr(
        pet_member_repo, "count_valid_invites", member_count_valid_invites
    )
    monkeypatch.setattr(pet_member_repo, "add_invite", member_add_invite)
    monkeypatch.setattr(pet_member_repo, "delete_invite", member_delete_invite)
    monkeypatch.setattr(
        pet_member_repo, "delete_expired_invites", member_delete_expired_invites
    )
    monkeypatch.setattr(pet_member_repo, "list_invites", member_list_invites)
    monkeypatch.setattr(
        pet_member_repo, "delete_invite_for_pet", member_delete_invite_for_pet
    )
    monkeypatch.setattr(
        pet_member_repo, "delete_invites_for_pet", member_delete_invites_for_pet
    )

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
        if analysis not in store.walk_analyses:
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
    monkeypatch.setattr(walk_repo, "get_owned_for_finalize", walk_get_owned)
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

    # -- care events (#332 · #344) ------------------------------------------
    # 비서의 오늘 요약(`services/care_log_context`)이 `active_dog_id` 요청마다 읽는 셋.
    # 정렬·필터 규칙은 진짜 리포지토리와 같습니다. 기록·삭제 쪽 대역은 `test_care_events.py`
    # 가 자기 파일 안에서 더 촘촘히 씁니다.
    def _in_window(at, start, end) -> bool:
        try:
            return start <= at < end
        except TypeError:
            # naive 시각을 넣은 옛 산책 대역 — 하루 창과 비교할 수 없으면 안 센다.
            return False

    def _care_between(_app_user_id, pet_id, start, end):
        # 진짜와 같게 **actor 로 안 거릅니다** — 돌봄 기록은 강아지 것이라, 사람으로 거르면
        # 다른 보호자가 적은 줄만 빠집니다 (docs/co-care.md §2).
        return [
            e for e in store.care_events
            if e.pet_id == pet_id and _in_window(e.occurred_at, start, end)
        ]

    async def care_list_between(session, app_user_id, pet_id, start, end):
        return sorted(
            _care_between(app_user_id, pet_id, start, end),
            key=lambda e: (e.occurred_at, e.id), reverse=True,
        )

    async def care_count_by_kind(session, app_user_id, pet_id, start, end):
        counts: dict[str, int] = {}
        for e in _care_between(app_user_id, pet_id, start, end):
            counts[e.kind] = counts.get(e.kind, 0) + 1
        return counts

    async def walk_count_for_pet_between(session, _app_user_id, pet_id, start, end):
        # 진짜와 같게 **소유자 조건이 없습니다** — 부르는 쪽이 이미 접근 권한을 봤고,
        # 여기서 다시 사람으로 거르면 다른 보호자의 산책만 빠집니다 (docs/co-care.md §2).
        return sum(
            1 for w in store.walks
            if pet_id in w.pet_ids and _in_window(w.started_at, start, end)
        )

    async def walk_activity_for_pet_between(session, pet_id, start, end):
        # 비서의 오늘 산책 요약(`services/walk_activity_context`, D-073)이 `active_dog_id`
        # 요청마다 읽는 값. **기본은 빈 하루** — `care_events`·`vet_visits` 와 같은 이유로
        # (위 `Store.__init__` 주석), 대역이 없으면 관련 없는 테스트가 진짜 리포지토리를
        # 타서 `FakeSession` 에서 죽는다. 측정 합계(거리·이동 시간·측정 건수)는 이 대역이
        # 흉내 내지 않는다 — 그 조합을 보는 테스트는 `test_assistant_walk_activity.py` 가
        # `walk_repo.activity_for_pet_between` 자체를 자기 파일 안에서 다시 monkeypatch
        # 해 직접 다룬다(`care_repo`/`walk_repo` 대역을 이 파일들이 덮어 쓰는 것과 같은 꼴).
        starts = [
            w.started_at for w in store.walks
            if pet_id in w.pet_ids and _in_window(w.started_at, start, end)
        ]
        return WalkActivitySums(
            walk_count=len(starts), measured_walk_count=0,
            distance_m=0, moving_s=0,
            last_started_at=max(starts) if starts else None,
        )

    monkeypatch.setattr(care_repo, "list_between", care_list_between)
    monkeypatch.setattr(care_repo, "count_by_kind", care_count_by_kind)
    monkeypatch.setattr(walk_repo, "count_for_pet_between", walk_count_for_pet_between)
    monkeypatch.setattr(walk_repo, "activity_for_pet_between", walk_activity_for_pet_between)

    # -- vet visits (#353 Task 7) --------------------------------------------
    # 비서의 최근 진료비 요약(`services/vet_spend_context`)이 `active_dog_id` 요청마다
    # 읽는 둘. 정렬 규칙은 진짜 리포지토리(최근 먼저)와 같습니다. `test_vet_visits.py` 는
    # 이 대역을 안 씁니다 — 그 파일은 실제 `select()` 를 해석하는 자기만의 얇은 세션
    # 대역을 씁니다(그 파일 머리말).
    def _vet_between(app_user_id, pet_id, start, end):
        return [
            v for v in store.vet_visits
            if v.app_user_id == app_user_id and v.pet_id == pet_id
            and start <= v.visited_on <= end
        ]

    async def vet_list_between(session, app_user_id, pet_id, start, end):
        # 진짜 리포지토리와 같은 순서: visited_on DESC, id ASC. 튜플째로 reverse=True
        # 하면 id 까지 뒤집혀 같은 날 두 건일 때 순서가 갈린다 — id 오름차순으로 먼저
        # 정렬한 뒤 visited_on 만 내림차순으로 다시 정렬한다(안정 정렬이라 동률의
        # 상대 순서가 유지된다).
        rows = sorted(_vet_between(app_user_id, pet_id, start, end), key=lambda v: v.id)
        rows.sort(key=lambda v: v.visited_on, reverse=True)
        return rows

    async def vet_sum_by_reason(session, app_user_id, pet_id, start, end):
        totals: dict[str, int] = {}
        for v in _vet_between(app_user_id, pet_id, start, end):
            totals[v.reason_code] = totals.get(v.reason_code, 0) + v.total_krw
        return totals

    monkeypatch.setattr(vet_repo, "list_between", vet_list_between)
    monkeypatch.setattr(vet_repo, "sum_by_reason", vet_sum_by_reason)

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

    async def get_accessible_pet_id(session, app_user_id, pet_id):
        # 진짜와 같게 **구성원(대표 ∪ 돌보미)** 입니다 (docs/co-care.md §2).
        ids = _member_pet_ids(app_user_id)
        return next(
            (
                p.id
                for p in store.pets
                if p.id == pet_id and (p.app_user_id == app_user_id or p.id in ids)
            ),
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

    async def list_recent_completed_turns(session, session_id, *, limit):
        """진짜와 같은 계약: completed 만, 최신 `limit`개, **오래된 순**으로 반환한다.

        진짜(`repositories/chat.py`)는 `ORDER BY created_at DESC, id DESC LIMIT` 한 뒤
        뒤집는다 — 여기서도 같은 두 단계(자르고 나서 뒤집기)를 거쳐야, 가짜와 DB 가
        같은 자리에서 잘라낸다(가장 오래된 것부터 버림)는 것을 이 페이크가 보장한다.
        """
        rows = [
            turn
            for turn in store.chat_turns
            if turn.session_id == session_id and turn.processing_status == "completed"
        ]
        newest_first = sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)
        return list(reversed(newest_first[:limit]))

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

    monkeypatch.setattr(chat_repo, "get_accessible_pet_id", get_accessible_pet_id)
    monkeypatch.setattr(chat_repo, "lock_accessible_pet", get_accessible_pet_id)
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
    monkeypatch.setattr(
        chat_repo, "list_recent_completed_turns", list_recent_completed_turns
    )
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

    # ── 피부 변화 기록 (D-052) ───────────────────────────────────────────
    #
    # 탈퇴가 이것을 명시로 지웁니다 — app_users 행을 남기므로 FK CASCADE 가 영영
    # 안 돕니다. 그래서 이 대역이 없으면 **탈퇴 테스트가 전부 깨집니다.**

    def screening_add(session, record):
        # 진짜 DB 는 `gen_random_uuid()` 와 `NOW()` 로 채웁니다. 가짜가 그 역할을
        # 합니다 — 안 채우면 응답 스키마가 created_at=None 에서 터집니다
        # (pet_add 가 id 를 채우는 것과 같은 자리).
        if getattr(record, "id", None) is None:
            record.id = uuid.uuid4()
        now = datetime.now(UTC)
        if record.created_at is None:
            record.created_at = now
        if record.updated_at is None:
            record.updated_at = now
        store.screenings.append(record)
        return record

    async def screening_get_owned(session, app_user_id, record_id, *, for_update=False):
        return next(
            (
                r
                for r in store.screenings
                if r.id == record_id and r.app_user_id == app_user_id
            ),
            None,
        )

    async def screening_list_for_owner(
        session, app_user_id, *, pet_id=None, before=None, limit=50
    ):
        rows = [r for r in store.screenings if r.app_user_id == app_user_id]
        if pet_id is not None:
            rows = [r for r in rows if r.pet_id == pet_id]
        # **담은 순서를 뒤집지 않고 `created_at` 으로 실제로 정렬합니다.** 뒤집기로 흉내 내면
        # "담은 순서 = 시간 순서" 인 테스트만 통과하고, 옛 기록을 기준으로 묻는 갈래가 통째로
        # 안 돌아 #79 3번의 순서 버그를 못 잡았습니다 (동시각은 진짜와 같게 `id` 로 가릅니다).
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        if before is not None:
            rows = [r for r in rows if (r.created_at, r.id) < (before.created_at, before.id)]
        return rows[:limit]

    def _screening_accessible_pet_ids(app_user_id):
        # `pet_repo.member_condition` 의 대역과 같은 모양입니다 (대표 ∪ 돌보미).
        return {
            p.id
            for p in store.pets
            if p.app_user_id == app_user_id
        } | {pid for pid, uid in store.pet_members if uid == app_user_id}

    async def screening_get_accessible(session, app_user_id, record_id):
        # 진짜와 같게 **창작자이거나, 강아지에 붙었고 내가 그 아이의 구성원**입니다.
        # `pet_id IS NULL` 인 개인 기록은 아무 구성원 집합에도 안 걸려 창작자만입니다.
        ids = _screening_accessible_pet_ids(app_user_id)
        return next(
            (
                r
                for r in store.screenings
                if r.id == record_id
                and (r.app_user_id == app_user_id or r.pet_id in ids)
            ),
            None,
        )

    async def screening_list_accessible(
        session, app_user_id, *, pet_id=None, before=None, limit=50
    ):
        ids = _screening_accessible_pet_ids(app_user_id)
        rows = [
            r
            for r in store.screenings
            if r.app_user_id == app_user_id or r.pet_id in ids
        ]
        if pet_id is not None:
            rows = [r for r in rows if r.pet_id == pet_id]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        if before is not None:
            rows = [r for r in rows if (r.created_at, r.id) < (before.created_at, before.id)]
        return rows[:limit]

    async def screening_get_deletable(session, app_user_id, record_id, *, for_update=False):
        # 진짜와 같게 **창작자 또는 그 아이의 대표**입니다 — 구성원 전체가 아닙니다
        # (docs/co-care.md §2, care_event 의 get_deletable 과 같은 모양).
        def _pet_owner(pet_id):
            return next((p.app_user_id for p in store.pets if p.id == pet_id), None)

        return next(
            (
                r
                for r in store.screenings
                if r.id == record_id
                and (
                    r.app_user_id == app_user_id
                    or (r.pet_id is not None and _pet_owner(r.pet_id) == app_user_id)
                )
            ),
            None,
        )

    async def screening_find_by_storage_key(session, storage_key, *, status=None):
        # 진짜와 같게 **소유자 조건이 없습니다** — bridge 는 인증 헤더를 안 받고
        # "backend 가 발급한 키인가" 만 봅니다.
        return next(
            (
                r
                for r in store.screenings
                if r.photo_storage_key == storage_key
                and (status is None or r.status == status)
            ),
            None,
        )

    async def screening_list_for_owner_for_update(session, app_user_id):
        return [r for r in store.screenings if r.app_user_id == app_user_id]

    async def screening_delete(session, record):
        store.screenings.remove(record)

    async def screening_delete_all_for_owner(session, app_user_id):
        mine = [r for r in store.screenings if r.app_user_id == app_user_id]
        store.screenings = [r for r in store.screenings if r.app_user_id != app_user_id]
        return len(mine)

    monkeypatch.setattr(screening_repo, "add", screening_add)
    monkeypatch.setattr(screening_repo, "get_owned", screening_get_owned)
    monkeypatch.setattr(screening_repo, "get_accessible", screening_get_accessible)
    monkeypatch.setattr(screening_repo, "list_for_owner", screening_list_for_owner)
    monkeypatch.setattr(screening_repo, "list_accessible", screening_list_accessible)
    monkeypatch.setattr(screening_repo, "get_deletable", screening_get_deletable)
    monkeypatch.setattr(screening_repo, "find_by_storage_key", screening_find_by_storage_key)
    monkeypatch.setattr(
        screening_repo, "list_for_owner_for_update", screening_list_for_owner_for_update
    )
    monkeypatch.setattr(screening_repo, "delete", screening_delete)
    monkeypatch.setattr(screening_repo, "delete_all_for_owner", screening_delete_all_for_owner)

    # ── 도감 카드 (D-052) ────────────────────────────────────────────────
    #
    # 탈퇴가 이것도 명시로 지웁니다 — app_users 행을 남기므로 FK CASCADE 가 영영
    # 안 돕니다. 그래서 이 대역이 없으면 **탈퇴 테스트가 깨집니다.**

    def card_add(session, card):
        # ⚠️ id 는 **앱이 만듭니다.** 다른 표와 달리 가짜가 채우지 않습니다 —
        #    채우면 "앱이 안 보냈을 때 서버가 새 id 를 만든다" 는, 진짜에는 없는
        #    동작을 테스트가 못 잡습니다.
        now = datetime.now(UTC)
        if card.created_at is None:
            card.created_at = now
        if card.updated_at is None:
            card.updated_at = now
        store.dog_cards.append(card)
        return card

    async def card_get_any(session, card_id, *, for_update=False):
        return next((c for c in store.dog_cards if c.id == card_id), None)

    async def card_get_owned(session, app_user_id, card_id, *, for_update=False):
        return next(
            (
                c
                for c in store.dog_cards
                if c.id == card_id and c.app_user_id == app_user_id
            ),
            None,
        )

    async def card_list_for_owner(session, app_user_id, *, limit=500):
        rows = [c for c in store.dog_cards if c.app_user_id == app_user_id]
        # 진짜는 drawn_at DESC 입니다.
        return sorted(rows, key=lambda c: c.drawn_at, reverse=True)[:limit]

    async def card_find_by_face_key(session, storage_key):
        return next(
            (c for c in store.dog_cards if c.face_storage_key == storage_key), None
        )

    async def card_list_for_owner_for_update(session, app_user_id):
        return [c for c in store.dog_cards if c.app_user_id == app_user_id]

    async def card_delete(session, card):
        store.dog_cards.remove(card)

    async def card_delete_all_for_owner(session, app_user_id):
        mine = [c for c in store.dog_cards if c.app_user_id == app_user_id]
        store.dog_cards = [c for c in store.dog_cards if c.app_user_id != app_user_id]
        return len(mine)

    monkeypatch.setattr(card_repo, "add", card_add)
    monkeypatch.setattr(card_repo, "get_any", card_get_any)
    monkeypatch.setattr(card_repo, "get_owned", card_get_owned)
    monkeypatch.setattr(card_repo, "list_for_owner", card_list_for_owner)
    monkeypatch.setattr(card_repo, "find_by_face_key", card_find_by_face_key)
    monkeypatch.setattr(card_repo, "list_for_owner_for_update", card_list_for_owner_for_update)
    monkeypatch.setattr(card_repo, "delete", card_delete)
    monkeypatch.setattr(card_repo, "delete_all_for_owner", card_delete_all_for_owner)

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

    # -----------------------------------------------------------------------
    # AI 답변 신고 (A1 · D-053)
    # -----------------------------------------------------------------------

    def _turn(turn_id):
        return next((t for t in store.chat_turns if t.id == turn_id), None)

    async def report_get_owned_turn(session, *, turn_id, app_user_id):
        """**소유 확인이 이 대역의 핵심입니다.** 진짜는 chat_sessions 조인으로 거르고,
        여기서도 같은 경로로 걸러야 "남의 turn 은 404" 를 테스트가 볼 수 있습니다."""
        turn = _turn(turn_id)
        if turn is None:
            return None
        owner = next(
            (c for c in store.chat_sessions if c.id == turn.session_id), None
        )
        if owner is None or owner.app_user_id != app_user_id:
            return None
        return turn

    async def report_add(session, *, turn_id, app_user_id, reason):
        """얹기만 합니다 — 진짜 `session.add()` 도 그렇습니다.

        중복은 `FakeSession.commit()` 이 터뜨립니다. 여기서 터뜨리면 서비스의
        `try: commit / except IntegrityError` 를 건너뛰어, **정작 지키려던 경로를
        테스트가 안 지나갑니다.**
        """
        report = FakeAnswerReport(
            turn_id=turn_id,
            app_user_id=app_user_id,
            reason=reason,
            created_at=store.tick(),
        )
        store.answer_reports_pending.append(report)
        return report

    async def report_get_by_id(session, report_id):
        return next((r for r in store.answer_reports if r.id == report_id), None)

    async def report_get_turn(session, turn_id):
        return _turn(turn_id)

    async def report_count_for_turn(session, turn_id):
        return sum(1 for r in store.answer_reports if r.turn_id == turn_id)

    async def report_count_for_turns(session, turn_ids):
        wanted = set(turn_ids)
        counts: dict = {}
        for r in store.answer_reports:
            if r.turn_id in wanted:
                counts[r.turn_id] = counts.get(r.turn_id, 0) + 1
        return counts

    async def report_turn_position(session, turn):
        done = sorted(
            (
                t
                for t in store.chat_turns
                if t.session_id == turn.session_id
                and t.processing_status == "completed"
            ),
            key=lambda t: (t.created_at, t.id),
        )
        if turn.processing_status != "completed":
            return len(done), 0
        return len(done), done.index(turn) + 1

    async def report_list_reports(session, *, limit, before=None, status=None):
        rows = sorted(
            store.answer_reports, key=lambda r: (r.created_at, r.id), reverse=True
        )
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if before is not None:
            rows = [r for r in rows if (r.created_at, r.id) < before]
        out = []
        for r in rows[:limit]:
            reviewer = next(
                (a for a in store.admins if a.id == r.reviewed_by), None
            )
            out.append(
                (r, getattr(reviewer, "login_id", None), getattr(reviewer, "name", None))
            )
        return out

    async def report_get_with_reviewer(session, report_id):
        report = next((r for r in store.answer_reports if r.id == report_id), None)
        if report is None:
            return None
        reviewer = next((a for a in store.admins if a.id == report.reviewed_by), None)
        return (
            report,
            getattr(reviewer, "login_id", None),
            getattr(reviewer, "name", None),
        )

    monkeypatch.setattr(answer_report_repo, "get_owned_turn", report_get_owned_turn)
    monkeypatch.setattr(answer_report_repo, "add", report_add)
    monkeypatch.setattr(answer_report_repo, "get_by_id", report_get_by_id)
    monkeypatch.setattr(answer_report_repo, "get_turn", report_get_turn)
    monkeypatch.setattr(answer_report_repo, "count_for_turn", report_count_for_turn)
    monkeypatch.setattr(answer_report_repo, "count_for_turns", report_count_for_turns)
    monkeypatch.setattr(answer_report_repo, "turn_position", report_turn_position)
    monkeypatch.setattr(answer_report_repo, "list_reports", report_list_reports)
    monkeypatch.setattr(
        answer_report_repo, "get_with_reviewer", report_get_with_reviewer
    )


    return store
