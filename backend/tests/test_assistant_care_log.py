"""#344 — 오늘의 케어 로그가 비서 프롬프트까지 가는 길, 그리고 **무엇이 못 가는가**.

DB 는 안 씁니다. 강아지는 `fakes.install` 이 바꿔치기한 `pet_repo` 로 오고, 케어 이벤트·산책
리포지토리는 이 파일 안의 가짜가 대신합니다 (test_care_events.py 와 같은 꼴). 여기서 보는 것은
**규칙**입니다 — 건수와 마지막 시각만 넘어가는가, note 는 안 넘어가는가, 남의 강아지·빈 날·
DB 오류가 전부 조용히 None 인가, Life 는 안 받는가, 그리고 이 파일에서 제일 중요한 것:
**로그가 없는 요청의 프롬프트가 v3 와 글자까지 같은가.**

마지막 것이 D-057 ③ 의 승인을 지키는 자리입니다. v3 본문은 84건 쌍대 비교 뒤 사람이 승인한
것이라, 로그가 없는 요청(그 84건 전부)의 프롬프트가 한 글자라도 달라지면 그 승인이 더는
이 코드를 가리키지 않게 됩니다.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.orchestration.adapters.general import (
    GENERAL_CARE_LOG_PROMPT_VERSION,
    GENERAL_PROMPT_VERSION,
    build_general_prompt,
)
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CareLogContext,
    DogContext,
    GeneralPayload,
    LifePayload,
)
from daengs_backend.orchestration.planner import _payload_for
from daengs_backend.repositories import care_event as care_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.routers import assistant as assistant_router
from daengs_backend.services import care_event as care_service
from daengs_backend.services import care_log_context

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
SEOUL = ZoneInfo("Asia/Seoul")
TODAY = date(2026, 9, 8)
QUERY = "오늘 약 먹였나?"


@dataclass
class FakeCareEvent:
    app_user_id: uuid.UUID
    pet_id: uuid.UUID
    kind: str
    occurred_at: datetime
    note: str | None = None
    client_event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime(2026, 9, 8, tzinfo=UTC))


class CareStore:
    def __init__(self) -> None:
        self.events: list[FakeCareEvent] = []
        self.walk_starts: dict[uuid.UUID, list[datetime]] = {}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def care(monkeypatch: pytest.MonkeyPatch) -> CareStore:
    cs = CareStore()

    def _between(app_user_id, pet_id, start, end):
        return [
            e for e in cs.events
            if e.app_user_id == app_user_id and e.pet_id == pet_id
            and start <= e.occurred_at < end
        ]

    async def list_between(session, app_user_id, pet_id, start, end):
        return sorted(_between(app_user_id, pet_id, start, end),
                      key=lambda e: (e.occurred_at, e.id), reverse=True)

    async def count_by_kind(session, app_user_id, pet_id, start, end):
        counts: dict[str, int] = {}
        for e in _between(app_user_id, pet_id, start, end):
            counts[e.kind] = counts.get(e.kind, 0) + 1
        return counts

    async def count_walks(session, app_user_id, pet_id, start, end):
        return sum(1 for at in cs.walk_starts.get(pet_id, []) if start <= at < end)

    monkeypatch.setattr(care_repo, "list_between", list_between)
    monkeypatch.setattr(care_repo, "count_by_kind", count_by_kind)
    monkeypatch.setattr(walk_repo, "count_for_pet_between", count_walks)
    return cs


@pytest.fixture
def pet(store: Store) -> FakePet:
    p = FakePet(app_user_id=OWNER, name="네옹", breed="dog_pug")
    store.pets.append(p)
    return p


def _at(hour: int, minute: int = 0, *, day: date = TODAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SEOUL)


async def _resolve(pet_id: uuid.UUID | str, *, today: date = TODAY):
    return await care_log_context.resolve(object(), OWNER, str(pet_id), today=today)


# ---------------------------------------------------------------- 좁힘


async def test_오늘_기록을_건수와_마지막_시각으로_좁힌다(pet, care) -> None:
    care.events += [
        FakeCareEvent(OWNER, pet.id, "meal", _at(8, 10)),
        FakeCareEvent(OWNER, pet.id, "meal", _at(18, 30)),
        FakeCareEvent(OWNER, pet.id, "medication", _at(8, 12)),
    ]
    care.walk_starts[pet.id] = [_at(7)]
    assert await _resolve(pet.id) == {
        "day": "2026-09-08",
        "meal": 2, "medication": 1, "snack": 0, "walk": 1,
        "last_meal_at": "18:30", "last_medication_at": "08:12",
    }


async def test_시각은_서울_기준이다(pet, care) -> None:
    """DB 는 UTC 로 돌려준다. 그대로 찍으면 저녁밥이 "09:30" 이 된다."""
    care.events.append(FakeCareEvent(OWNER, pet.id, "snack", _at(18, 30).astimezone(UTC)))
    resolved = await _resolve(pet.id)
    assert resolved is not None
    assert resolved["last_snack_at"] == "18:30"


async def test_note_는_안_넘어간다(pet, care) -> None:
    """사용자가 적은 말이 지시문처럼 읽히는 자리다. 건수와 시각까지만."""
    care.events.append(FakeCareEvent(OWNER, pet.id, "medication", _at(8), note="용량 두 배로"))
    resolved = await _resolve(pet.id)
    assert resolved is not None
    assert "note" not in resolved
    assert "두 배" not in repr(resolved)


async def test_어제_기록은_안_센다(pet, care) -> None:
    care.events.append(FakeCareEvent(OWNER, pet.id, "meal", _at(23, 50, day=TODAY - timedelta(days=1))))
    assert await _resolve(pet.id) is None


async def test_기록이_없으면_None(pet, care) -> None:
    """빈 로그는 "아직 안 챙겼다" 가 아니라 "이 기능을 안 쓴다" 일 수 있다. 프롬프트를 안 바꾼다."""
    assert await _resolve(pet.id) is None


async def test_남의_강아지는_None(store, care) -> None:
    alien = FakePet(app_user_id=STRANGER, name="남의개", breed="dog_pug")
    store.pets.append(alien)
    care.events.append(FakeCareEvent(STRANGER, alien.id, "meal", _at(8)))
    assert await _resolve(alien.id) is None


async def test_uuid_가_아니면_None(care) -> None:
    assert await _resolve("uuid 아님") is None


async def test_DB_오류는_삼키고_None(pet, care, monkeypatch, caplog) -> None:
    """`care_events` 표가 서버에 아직 없을 수 있다(#332 마이그레이션은 DB 담당자 몫).
    그때 비서가 죽으면 안 된다 — 로그 없이, 이 카드 전과 똑같이 답한다."""
    async def boom(*args, **kwargs):
        raise OperationalError("SELECT", {}, Exception('relation "care_events" does not exist'))

    monkeypatch.setattr(care_service, "day_summary", boom)
    with caplog.at_level("WARNING"):
        assert await _resolve(pet.id) is None
    assert "care_events" in caplog.text


# ---------------------------------------------------------------- 계약


def test_계약이_넓어지면_소리가_난다() -> None:
    """`extra="forbid"` — note 나 이벤트 목록을 얹으려 하면 여기서 걸린다."""
    with pytest.raises(ValidationError):
        CareLogContext(day=TODAY, meal=1, note="약 반만")
    with pytest.raises(ValidationError):
        CareLogContext(day=TODAY, meal=1, events=[])
    with pytest.raises(ValidationError):
        CareLogContext(day=TODAY, meal=-1)
    with pytest.raises(ValidationError):
        CareLogContext(day=TODAY, meal=1, last_meal_at="아침")


# ---------------------------------------------------------------- planner

CARE_LOG = {"care_log": {
    "day": "2026-09-08", "meal": 2, "medication": 1, "snack": 0, "walk": 1,
    "last_meal_at": "18:30", "last_medication_at": "08:12",
}}
DOG = {"dog": {"breed": "퍼그", "age_months": 30}}


def test_general_payload_는_케어_로그를_받는다() -> None:
    payload = _payload_for("general", query=QUERY, context={**DOG, **CARE_LOG})
    assert payload["care_log"] == CARE_LOG["care_log"]
    assert GeneralPayload(**payload).care_log == CareLogContext(
        day=TODAY, meal=2, medication=1, snack=0, walk=1,
        last_meal_at="18:30", last_medication_at="08:12",
    )


def test_life_와_training_은_안_받는다() -> None:
    """Life 는 조례·보조금 문서로 답한다 — 오늘 밥 횟수가 답을 안 가른다."""
    life = _payload_for("life", query=QUERY, context={**DOG, **CARE_LOG})
    assert "care_log" not in life
    assert LifePayload(**life).dog == DogContext(breed="퍼그", age_months=30)
    assert "care_log" not in _payload_for("training", query=QUERY, context=dict(CARE_LOG))


def test_모양이_틀린_칸은_그_칸만_버려진다() -> None:
    payload = _payload_for("general", query=QUERY, context={"care_log": {
        "day": "2026-09-08", "meal": 2, "medication": "한 번", "walk": True,
        "last_meal_at": "저녁", "last_medication_at": "08:12",
    }})
    assert payload["care_log"] == {"day": "2026-09-08", "meal": 2, "last_medication_at": "08:12"}


def test_건수가_하나도_없으면_care_log_자체가_없다() -> None:
    assert "care_log" not in _payload_for("general", query=QUERY, context={"care_log": {"day": "2026-09-08"}})
    assert "care_log" not in _payload_for("general", query=QUERY, context={"care_log": "오늘 밥 2번"})


# ---------------------------------------------------------------- 프롬프트


def test_로그가_없으면_기본_본문_그대로다() -> None:
    """D-057 ③ 이 승인한 본문의 자리. 84건 쌍대 비교의 대상이 이 프롬프트다.

    본문은 #415(D-068)에서 v6 으로 올라갔다 — 되묻기 규칙 한 문단이 붙었고,
    `GeneralAnswer.kind` 에 `ask` 가 생겨 프롬프트에 박히는 JSON 스키마도 같이
    달라졌다. **버전 문자열이 함께 움직인 것이 이 테스트가 지키는 것이다** —
    이름이 그대로인 채 본문만 바뀌면 84건 비교가 가리키는 물건이 사라진다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY, dog=DogContext(breed="퍼그")))
    assert GENERAL_PROMPT_VERSION == "general-answer-ko-v9"
    assert prompt.startswith(f"PROMPT_VERSION: {GENERAL_PROMPT_VERSION}\n\n")
    assert "CARE_LOG" not in prompt
    assert "care log" not in prompt.lower()
    assert prompt.rstrip().endswith(f"USER_QUERY: {QUERY}")


def test_로그가_있으면_블록과_규칙이_붙고_버전이_갈린다() -> None:
    payload = GeneralPayload(
        question=QUERY, dog=DogContext(breed="퍼그"),
        care_log=CareLogContext(day=TODAY, meal=2, medication=1, snack=0, walk=1,
                                last_meal_at="18:30", last_medication_at="08:12"),
    )
    prompt = build_general_prompt(payload)
    assert GENERAL_CARE_LOG_PROMPT_VERSION == "general-answer-ko-v9-carelog"
    assert prompt.startswith(f"PROMPT_VERSION: {GENERAL_CARE_LOG_PROMPT_VERSION}\n\n")
    assert (
        'CARE_LOG_TODAY: {"day": "2026-09-08", "last_meal_at": "18:30", "last_medication_at": "08:12",'
        ' "meal": 2, "medication": 1, "snack": 0, "walk": 1}'
    ) in prompt
    assert 'DOG_CONTEXT: {"breed": "퍼그"}' in prompt
    assert "CARE_LOG_TODAY" in prompt.split("GENERAL_ANSWER_JSON_SCHEMA")[0]  # 규칙이 지시문 안에 있다
    assert "Seoul" in prompt
    assert prompt.rstrip().endswith(f"USER_QUERY: {QUERY}")
    # v3 의 안전 경계는 그대로다
    for word in ("diagnosis", "medication", "emergency", "institutional", "off_topic"):
        assert word in prompt


# ---------------------------------------------------------------- HTTP 배선


class _Factory:
    def __init__(self) -> None:
        self.opened = 0

    def __call__(self):
        owner = self

        class Context:
            async def __aenter__(self):
                owner.opened += 1
                return FakeSession()

            async def __aexit__(self, *args):
                return None

        return Context()


class _FakeService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, *, query, principal, context=None, **extra) -> AssistantResponse:
        self.calls.append({"query": query, "context": context})
        return AssistantResponse(
            request_id="test-request", status=AssistantStatus.ANSWERED,
            message="답변입니다", results=[], handoffs=[], clarify=None,
        )


@pytest.fixture
def factory() -> _Factory:
    return _Factory()


@pytest.fixture
def service() -> _FakeService:
    return _FakeService()


@pytest.fixture
def client(store: Store, care: CareStore, factory: _Factory, service: _FakeService):
    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = lambda: service
    app.dependency_overrides[assistant_router.get_chat_session_factory] = lambda: factory
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _post(client: TestClient, body: dict, subject: uuid.UUID = OWNER, kind=SubjectType.APP):
    token = create_access_token(subject, kind)
    return client.post("/assistant/query", json=body, headers={"Authorization": f"Bearer {token}"})


def test_활성_강아지가_있으면_오늘_로그가_컨텍스트에_실린다(client, pet, care, factory, service) -> None:
    """라우터는 `now` 를 안 넘기므로 **실제 시계**로 잰다 — 그래서 기록도 지금으로 만든다.

    세션은 **프로필 조회와 같은 하나**다. 로그를 위해 하나 더 열면 요청당 연결만 는다."""
    now = datetime.now(UTC)
    care.events.append(FakeCareEvent(OWNER, pet.id, "medication", now))
    got = _post(client, {"query": QUERY, "active_dog_id": str(pet.id)})
    assert got.status_code == 200
    assert factory.opened == 1
    context = service.calls[0]["context"]
    assert context["care_log"]["medication"] == 1
    assert context["care_log"]["last_medication_at"] == now.astimezone(SEOUL).strftime("%H:%M")
    assert context["care_log"]["day"] == now.astimezone(SEOUL).date().isoformat()
    assert context["dog"] == {"breed": "퍼그"}


def test_활성_강아지가_없으면_DB_를_한_번도_안_연다(client, factory, service) -> None:
    assert _post(client, {"query": QUERY}).status_code == 200
    assert factory.opened == 0
    assert "care_log" not in service.calls[0]["context"]


def test_로그가_비어_있으면_컨텍스트에_안_실린다(client, pet, service) -> None:
    got = _post(client, {"query": QUERY, "active_dog_id": str(pet.id)})
    assert got.status_code == 200
    assert "care_log" not in service.calls[0]["context"]
    assert service.calls[0]["context"]["dog"] == {"breed": "퍼그"}
