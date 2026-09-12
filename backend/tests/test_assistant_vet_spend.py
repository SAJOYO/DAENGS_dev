"""#353 Task 7 — 확정된 진료비 기록이 비서 프롬프트까지 가는 길, 그리고 **무엇이 못 가는가**.

`test_assistant_care_log.py`(#344) 를 그대로 본뜬다 — 이 기능이 그 카드의 짝이기
때문이다. DB 는 안 씁니다. 강아지는 `fakes.install` 이 바꿔치기한 `pet_repo` 로 오고,
진료비 리포지토리는 이 파일 안의 가짜가 대신합니다 (`fakes.py` 의 기본 대역을 덮어써서
정렬·필터 규칙을 이 파일에서 통제합니다).

여기서 보는 것은 **규칙**입니다 — 이번 달 합계·최근 30일 건수·마지막 방문·사유별
12개월 누계만 넘어가는가, `reason_detail`·`raw_ocr_items`·`hospital_address` 는
안 넘어가는가, 남의 강아지·기록 없음·DB 오류가 전부 조용히 None 인가, Life·Training 은
안 받는가, 그리고 이 파일에서 제일 중요한 것: **네 조합(care_log × vet_spend)의
프롬프트가 각각 기대한 버전과 본문으로 나가고, 로그·진료비가 둘 다 없는 요청의 프롬프트가
v3 와 글자까지 같은가.**

마지막 것이 D-057 ③ 의 승인을 지키는 자리다 (#344 의 같은 문단).
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

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
    GENERAL_CARE_LOG_VET_PROMPT_VERSION,
    GENERAL_PROMPT_VERSION,
    GENERAL_VET_PROMPT_VERSION,
    GeneralAnswer,
    _CARE_LOG_RULE,
    _SAFETY_PROMPT,
    _VET_SPEND_RULE,
    build_general_prompt,
)
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CareLogContext,
    DogContext,
    GeneralPayload,
    LastVetVisitContext,
    LifePayload,
    TrainingPayload,
    VetSpendContext,
)
from daengs_backend.orchestration.planner import _payload_for
from daengs_backend.repositories import vet_visit as vet_repo
from daengs_backend.routers import assistant as assistant_router
from daengs_backend.services import vet_spend_context

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
TODAY = date(2026, 9, 10)
QUERY = "피부로 1년간 얼마 썼지?"


@dataclass
class FakeVetVisit:
    app_user_id: uuid.UUID
    pet_id: uuid.UUID
    reason_code: str
    visited_on: date
    total_krw: int
    hospital_name: str | None = None
    hospital_phone: str | None = None
    reason_detail: str | None = None
    raw_ocr_items: list = field(default_factory=list)
    hospital_address: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


class VetStore:
    def __init__(self) -> None:
        self.visits: list[FakeVetVisit] = []


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def vet(monkeypatch: pytest.MonkeyPatch) -> VetStore:
    vs = VetStore()

    def _between(app_user_id, pet_id, start, end):
        return [
            v for v in vs.visits
            if v.app_user_id == app_user_id and v.pet_id == pet_id
            and start <= v.visited_on <= end
        ]

    async def list_between(session, app_user_id, pet_id, start, end):
        # 진짜 리포지토리와 같은 순서: visited_on DESC, id ASC. 튜플을 통째로
        # reverse=True 하면 id 까지 뒤집혀 같은 날 두 건일 때 순서가 갈린다 — 먼저
        # id 로 오름차순 정렬한 뒤 visited_on 으로만 내림차순 정렬한다. 파이썬 sort 는
        # 안정 정렬이라 reverse=True 에서도 동률(같은 날)의 상대 순서(id 오름차순)가
        # 유지된다.
        rows = sorted(_between(app_user_id, pet_id, start, end), key=lambda v: v.id)
        rows.sort(key=lambda v: v.visited_on, reverse=True)
        return rows

    async def sum_by_reason(session, app_user_id, pet_id, start, end):
        totals: dict[str, int] = {}
        for v in _between(app_user_id, pet_id, start, end):
            totals[v.reason_code] = totals.get(v.reason_code, 0) + v.total_krw
        return totals

    monkeypatch.setattr(vet_repo, "list_between", list_between)
    monkeypatch.setattr(vet_repo, "sum_by_reason", sum_by_reason)
    return vs


@pytest.fixture
def pet(store: Store) -> FakePet:
    p = FakePet(app_user_id=OWNER, name="네옹", breed="dog_pug")
    store.pets.append(p)
    return p


async def _resolve(pet_id: uuid.UUID | str, *, today: date = TODAY):
    return await vet_spend_context.resolve(object(), OWNER, str(pet_id), today=today)


# ---------------------------------------------------------------- 좁힘


async def test_이번_달_합계와_최근_30일_건수와_마지막_방문을_넘긴다(pet, vet) -> None:
    vet.visits += [
        FakeVetVisit(OWNER, pet.id, "skin", date(2026, 9, 2), 80_000,
                     hospital_name="○○동물병원", hospital_phone="02-123-4567"),
        FakeVetVisit(OWNER, pet.id, "skin", date(2026, 7, 20), 100_000),
        FakeVetVisit(OWNER, pet.id, "vaccination", date(2026, 1, 5), 80_000),
    ]
    resolved = await _resolve(pet.id)
    assert resolved == {
        "month_total_krw": 80_000,
        "visit_count_30d": 1,
        "last_visit": {
            "date": "2026-09-02", "reason": "피부", "total_krw": 80_000,
            "hospital": "○○동물병원", "phone": "02-123-4567",
        },
        "by_reason_12m": {"피부": 180_000, "예방접종": 80_000},
    }


async def test_같은_날_두_건이면_id_오름차순으로_첫_행을_고른다(pet, vet) -> None:
    """진짜 리포지토리는 `ORDER BY visited_on DESC, id`(id ASC) 다. 가짜가 튜플째
    reverse=True 로 정렬하면 id 까지 뒤집혀 같은 날 두 건일 때 서로 다른 행을 고른다 —
    두 구현이 같은 행을 고르는지 여기서 고정한다."""
    small_id = uuid.UUID(int=1)
    large_id = uuid.UUID(int=2)
    vet.visits += [
        FakeVetVisit(OWNER, pet.id, "vaccination", date(2026, 9, 2), 50_000, id=large_id),
        FakeVetVisit(OWNER, pet.id, "skin", date(2026, 9, 2), 80_000, id=small_id),
    ]
    resolved = await _resolve(pet.id)
    assert resolved is not None
    assert resolved["last_visit"]["reason"] == "피부"  # id 가 작은 쪽


async def test_병원_이름과_전화가_없으면_그_칸만_빠진다(pet, vet) -> None:
    vet.visits.append(FakeVetVisit(OWNER, pet.id, "skin", date(2026, 9, 2), 80_000))
    resolved = await _resolve(pet.id)
    assert resolved is not None
    assert "hospital" not in resolved["last_visit"]
    assert "phone" not in resolved["last_visit"]


async def test_reason_detail_은_안_넘어간다(pet, vet) -> None:
    """유저가 적은 자유 텍스트다 — #344 가 note 를 뺀 것과 같은 이유."""
    vet.visits.append(
        FakeVetVisit(OWNER, pet.id, "skin", date(2026, 9, 2), 80_000,
                     reason_detail="발바닥 사이 진물")
    )
    resolved = await _resolve(pet.id)
    assert resolved is not None
    assert "reason_detail" not in resolved["last_visit"]
    assert "발바닥" not in repr(resolved)
    # 실제로 모델에 가는 것은 이 딕셔너리가 아니라 프롬프트 문자열이다 — 거기까지 확인한다.
    payload = GeneralPayload(question=QUERY, vet_spend=VetSpendContext(**resolved))
    assert "발바닥" not in build_general_prompt(payload)


async def test_raw_ocr_items_는_안_넘어간다(pet, vet) -> None:
    vet.visits.append(
        FakeVetVisit(OWNER, pet.id, "skin", date(2026, 9, 2), 80_000,
                     raw_ocr_items=[{"name": "초진료", "amount_krw": 15_000}])
    )
    resolved = await _resolve(pet.id)
    assert resolved is not None
    assert "raw_ocr_items" not in resolved["last_visit"]
    assert "초진료" not in repr(resolved)
    payload = GeneralPayload(question=QUERY, vet_spend=VetSpendContext(**resolved))
    assert "초진료" not in build_general_prompt(payload)


async def test_hospital_address_는_안_넘어간다(pet, vet) -> None:
    """이름·전화만 간다 — 주소는 프롬프트에서 할 일이 없다."""
    vet.visits.append(
        FakeVetVisit(OWNER, pet.id, "skin", date(2026, 9, 2), 80_000,
                     hospital_address="서울시 강남구 역삼동 123")
    )
    resolved = await _resolve(pet.id)
    assert resolved is not None
    assert "hospital_address" not in resolved["last_visit"]
    assert "address" not in resolved["last_visit"]
    assert "역삼동" not in repr(resolved)
    payload = GeneralPayload(question=QUERY, vet_spend=VetSpendContext(**resolved))
    assert "역삼동" not in build_general_prompt(payload)


async def test_기록이_없으면_None(pet, vet) -> None:
    assert await _resolve(pet.id) is None


async def test_resolve_is_none_for_other_persons_dog(store, vet) -> None:
    alien = FakePet(app_user_id=STRANGER, name="남의개", breed="dog_pug")
    store.pets.append(alien)
    vet.visits.append(FakeVetVisit(STRANGER, alien.id, "skin", date(2026, 9, 2), 80_000))
    assert await _resolve(alien.id) is None


async def test_uuid_가_아니면_None(vet) -> None:
    assert await _resolve("uuid 아님") is None


async def test_resolve_is_none_on_sqlalchemy_error(pet, vet, monkeypatch, caplog) -> None:
    """`vet_visits` 표가 서버에 아직 없을 수 있다(#353 마이그레이션은 배포 담당자 몫).
    그때 비서가 죽으면 안 된다 — 로그 없이, 이 카드 전과 똑같이 답한다."""
    async def boom(*args, **kwargs):
        raise OperationalError("SELECT", {}, Exception('relation "vet_visits" does not exist'))

    monkeypatch.setattr(vet_repo, "list_between", boom)
    with caplog.at_level("WARNING"):
        assert await _resolve(pet.id) is None
    assert "vet_visits" in caplog.text


async def test_by_reason_uses_display_labels(pet, vet) -> None:
    """코드가 아니라 표시명이다 — `last_visit.reason` 과 같은 이름을 써야 한 프롬프트
    안에서 같은 것이 두 이름으로 안 보인다."""
    vet.visits += [
        FakeVetVisit(OWNER, pet.id, "skin", date(2026, 9, 2), 320_000),
        FakeVetVisit(OWNER, pet.id, "vaccination", date(2026, 1, 5), 80_000),
    ]
    resolved = await _resolve(pet.id)
    assert resolved is not None
    assert resolved["by_reason_12m"] == {"피부": 320_000, "예방접종": 80_000}
    assert "skin" not in resolved["by_reason_12m"]
    assert "vaccination" not in resolved["by_reason_12m"]


# ---------------------------------------------------------------- 계약


def test_contract_forbids_extra_fields() -> None:
    """`extra="forbid"` — 새 칸을 얹으려 하면 여기서 걸린다."""
    last_visit = LastVetVisitContext(date=TODAY, reason="피부", total_krw=80_000)
    with pytest.raises(ValidationError):
        VetSpendContext(
            month_total_krw=0, visit_count_30d=0, last_visit=last_visit,
            emergency_count_12m=1,
        )
    with pytest.raises(ValidationError):
        VetSpendContext(month_total_krw=-1, visit_count_30d=0, last_visit=last_visit)
    with pytest.raises(ValidationError):
        LastVetVisitContext(date=TODAY, reason="피부", total_krw=80_000, reason_detail="x")
    with pytest.raises(ValidationError):
        LastVetVisitContext(date=TODAY, reason="피부", total_krw=-1)


# ---------------------------------------------------------------- planner

VET_SPEND = {"vet_spend": {
    "month_total_krw": 80_000, "visit_count_30d": 1,
    "last_visit": {"date": "2026-09-02", "reason": "피부", "total_krw": 80_000,
                   "hospital": "○○동물병원", "phone": "02-123-4567"},
    "by_reason_12m": {"피부": 320_000, "예방접종": 80_000},
}}
DOG = {"dog": {"breed": "퍼그", "age_months": 30}}


def test_planner_sends_vet_spend_to_general_only() -> None:
    payload = _payload_for("general", query=QUERY, context={**DOG, **VET_SPEND})
    assert payload["vet_spend"] == VET_SPEND["vet_spend"]
    assert GeneralPayload(**payload).vet_spend == VetSpendContext(
        month_total_krw=80_000, visit_count_30d=1,
        last_visit=LastVetVisitContext(
            date=date(2026, 9, 2), reason="피부", total_krw=80_000,
            hospital="○○동물병원", phone="02-123-4567",
        ),
        by_reason_12m={"피부": 320_000, "예방접종": 80_000},
    )


def test_planner_drops_vet_spend_for_life() -> None:
    """Life 는 조례·보조금 문서로 답한다 — 이번 달 병원비가 답을 안 가른다."""
    life = _payload_for("life", query=QUERY, context={**DOG, **VET_SPEND})
    assert "vet_spend" not in life
    assert LifePayload(**life).dog == DogContext(breed="퍼그", age_months=30)
    training = _payload_for("training", query=QUERY, context=dict(VET_SPEND))
    assert "vet_spend" not in training
    TrainingPayload(**training)


def test_모양이_틀린_칸은_전체_블록이_빠진다() -> None:
    payload = _payload_for("general", query=QUERY, context={"vet_spend": {
        "month_total_krw": "많이", "visit_count_30d": 1,
        "last_visit": {"date": "2026-09-02", "reason": "피부", "total_krw": 80_000},
    }})
    assert "vet_spend" not in payload

    payload = _payload_for("general", query=QUERY, context={"vet_spend": {
        "month_total_krw": 0, "visit_count_30d": 0,
        "last_visit": {"date": "2026-09-02", "reason": "피부"},  # total_krw 없음
    }})
    assert "vet_spend" not in payload


def test_by_reason_의_잘못된_항목만_버려진다() -> None:
    payload = _payload_for("general", query=QUERY, context={"vet_spend": {
        "month_total_krw": 0, "visit_count_30d": 0,
        "last_visit": {"date": "2026-09-02", "reason": "피부", "total_krw": 80_000},
        "by_reason_12m": {"피부": 320_000, "예방접종": "많이", "": 10},
    }})
    assert payload["vet_spend"]["by_reason_12m"] == {"피부": 320_000}


# ---------------------------------------------------------------- 프롬프트


def test_prompt_without_vet_spend_keeps_the_base_body_byte_identical() -> None:
    """진료비가 없는 요청은 기본 본문과, 로그만 있는 요청은 로그 판본과 글자까지 같다 —
    진료비 블록이 한 칸도 새지 않는다.

    **한 자도 안 빠뜨리려고, 기대값을 `_SAFETY_PROMPT`/`_CARE_LOG_RULE`/스키마/컨텍스트
    줄에서 직접 다시 짓는다.** 버전 접두사와 부분 문자열만 보면, 리팩터로 두 판본
    사이에 조용히 공백 한 칸이 늘거나 줄어도 이 테스트가 못 잡는다 — D-057 ③ 의 84건
    쌍대 비교가 실제로 지키는 것은 이 바이트들이다."""
    schema = json.dumps(GeneralAnswer.model_json_schema(), ensure_ascii=False, sort_keys=True)
    dog_json = json.dumps({"breed": "퍼그"}, ensure_ascii=False, sort_keys=True)

    v3 = build_general_prompt(GeneralPayload(question=QUERY, dog=DogContext(breed="퍼그")))
    assert GENERAL_PROMPT_VERSION == "general-answer-ko-v7"
    expected_v3 = (
        f"PROMPT_VERSION: {GENERAL_PROMPT_VERSION}\n\n"
        f"{_SAFETY_PROMPT}\n\n"
        f"GENERAL_ANSWER_JSON_SCHEMA:\n{schema}\n\n"
        f"DOG_CONTEXT: {dog_json}\n"
        f"USER_QUERY: {QUERY}\n"
    )
    assert v3 == expected_v3
    assert "CARE_LOG" not in v3
    assert "VET_RECENT" not in v3
    assert "care log" not in v3.lower()

    care_log = CareLogContext(day=TODAY, meal=2, medication=1, snack=0, walk=1,
                              last_meal_at="18:30", last_medication_at="08:12")
    payload = GeneralPayload(question=QUERY, dog=DogContext(breed="퍼그"), care_log=care_log)
    v4 = build_general_prompt(payload)
    assert GENERAL_CARE_LOG_PROMPT_VERSION == "general-answer-ko-v7-carelog"
    care_log_json = json.dumps(
        care_log.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True
    )
    expected_v4 = (
        f"PROMPT_VERSION: {GENERAL_CARE_LOG_PROMPT_VERSION}\n\n"
        f"{_SAFETY_PROMPT}\n\n"
        f"{_CARE_LOG_RULE}\n\n"
        f"GENERAL_ANSWER_JSON_SCHEMA:\n{schema}\n\n"
        f"DOG_CONTEXT: {dog_json}\n"
        f"CARE_LOG_TODAY: {care_log_json}\n"
        f"USER_QUERY: {QUERY}\n"
    )
    assert v4 == expected_v4
    assert "VET_RECENT" not in v4


def test_prompt_version_flips_when_vet_spend_present() -> None:
    vet_spend = VetSpendContext(
        month_total_krw=180_000, visit_count_30d=2,
        last_visit=LastVetVisitContext(
            date=date(2026, 9, 2), reason="피부", total_krw=80_000,
            hospital="○○동물병원", phone="02-123-4567",
        ),
        by_reason_12m={"피부": 320_000, "예방접종": 80_000},
    )

    schema = json.dumps(GeneralAnswer.model_json_schema(), ensure_ascii=False, sort_keys=True)
    dog_json = json.dumps({"breed": "퍼그"}, ensure_ascii=False, sort_keys=True)
    vet_spend_json = json.dumps(
        vet_spend.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True
    )

    only_vet = build_general_prompt(
        GeneralPayload(question=QUERY, dog=DogContext(breed="퍼그"), vet_spend=vet_spend)
    )
    assert GENERAL_VET_PROMPT_VERSION == "general-answer-ko-v7-vetspend"
    expected_only_vet = (
        f"PROMPT_VERSION: {GENERAL_VET_PROMPT_VERSION}\n\n"
        f"{_SAFETY_PROMPT}\n\n"
        f"{_VET_SPEND_RULE}\n\n"
        f"GENERAL_ANSWER_JSON_SCHEMA:\n{schema}\n\n"
        f"DOG_CONTEXT: {dog_json}\n"
        f"VET_RECENT: {vet_spend_json}\n"
        f"USER_QUERY: {QUERY}\n"
    )
    assert only_vet == expected_only_vet
    assert "CARE_LOG_TODAY" not in only_vet

    care_log = CareLogContext(day=TODAY, meal=1)
    both = build_general_prompt(
        GeneralPayload(
            question=QUERY, dog=DogContext(breed="퍼그"), care_log=care_log, vet_spend=vet_spend
        )
    )
    assert GENERAL_CARE_LOG_VET_PROMPT_VERSION == "general-answer-ko-v7-carelog-vetspend"
    care_log_json = json.dumps(
        care_log.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True
    )
    expected_both = (
        f"PROMPT_VERSION: {GENERAL_CARE_LOG_VET_PROMPT_VERSION}\n\n"
        f"{_SAFETY_PROMPT}\n\n"
        f"{_CARE_LOG_RULE}\n\n"
        f"{_VET_SPEND_RULE}\n\n"
        f"GENERAL_ANSWER_JSON_SCHEMA:\n{schema}\n\n"
        f"DOG_CONTEXT: {dog_json}\n"
        f"CARE_LOG_TODAY: {care_log_json}\n"
        f"VET_RECENT: {vet_spend_json}\n"
        f"USER_QUERY: {QUERY}\n"
    )
    assert both == expected_both
    # 순서: CARE_LOG_TODAY 가 VET_RECENT 보다 먼저다(그 자리를 의도한 줄 순서로 다시 확인).
    assert both.index("CARE_LOG_TODAY") < both.index("VET_RECENT")


def test_모든_네_조합의_프롬프트_버전() -> None:
    """네 조합을 한자리에서 고정한다 — 다음 사람이 실수로 못 깨게."""
    dog = DogContext(breed="퍼그")
    care_log = CareLogContext(day=TODAY, meal=1)
    vet_spend = VetSpendContext(
        month_total_krw=0, visit_count_30d=0,
        last_visit=LastVetVisitContext(date=TODAY, reason="피부", total_krw=1),
    )

    cases = [
        (None, None, GENERAL_PROMPT_VERSION),
        (care_log, None, GENERAL_CARE_LOG_PROMPT_VERSION),
        (None, vet_spend, GENERAL_VET_PROMPT_VERSION),
        (care_log, vet_spend, GENERAL_CARE_LOG_VET_PROMPT_VERSION),
    ]
    versions = set()
    for log, spend, expected_version in cases:
        payload = GeneralPayload(question=QUERY, dog=dog, care_log=log, vet_spend=spend)
        prompt = build_general_prompt(payload)
        assert prompt.startswith(f"PROMPT_VERSION: {expected_version}\n\n")
        versions.add(expected_version)
    assert len(versions) == 4  # 네 조합이 네 개의 다른 버전 문자열이다


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
def client(store: Store, vet: VetStore, factory: _Factory, service: _FakeService):
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


def test_http_loads_vet_spend_in_the_same_session(client, pet, vet, factory, service) -> None:
    """세션은 **프로필 조회 · 케어 로그와 같은 하나**다. 진료비를 위해 하나 더 열면
    요청당 연결만 는다 (#344 가 `_with_care_log` 를 따로 뒀다가 깨뜨린 단언과 같다)."""
    vet.visits.append(
        FakeVetVisit(OWNER, pet.id, "skin", date(2026, 9, 2), 80_000,
                     hospital_name="○○동물병원", hospital_phone="02-123-4567")
    )
    got = _post(client, {"query": QUERY, "active_dog_id": str(pet.id)})
    assert got.status_code == 200
    assert factory.opened == 1
    context = service.calls[0]["context"]
    assert context["vet_spend"]["last_visit"]["reason"] == "피부"
    assert context["dog"] == {"breed": "퍼그"}


def test_활성_강아지가_없으면_DB_를_한_번도_안_연다(client, factory, service) -> None:
    assert _post(client, {"query": QUERY}).status_code == 200
    assert factory.opened == 0
    assert "vet_spend" not in service.calls[0]["context"]


def test_기록이_없으면_컨텍스트에_안_실린다(client, pet, service) -> None:
    got = _post(client, {"query": QUERY, "active_dog_id": str(pet.id)})
    assert got.status_code == 200
    assert "vet_spend" not in service.calls[0]["context"]
    assert service.calls[0]["context"]["dog"] == {"breed": "퍼그"}
