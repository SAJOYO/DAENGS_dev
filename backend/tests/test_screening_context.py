"""#307 — 판정 기록이 오케스트레이션 컨텍스트까지 가는 길, 그리고 **무엇이 못 가는가**.

DB 는 안 씁니다. `fakes.py` 가 리포지토리를 바꿔치기하므로 여기서 보는 것은 **규칙**입니다 —
남의 기록을 읽는가, 판정 전 기록을 아는 척하는가, 그리고 이 파일에서 제일 중요한 것:
**분포 · 계열 · 통제 문구 · 확률이 좁힘을 통과하는가.**

마지막 것이 D-023 을 코드로 붙잡는 자리입니다. 지금 그 방어가 서 있는 이유는 병변 이름을
말하는 코드 경로가 **없기** 때문인데, 좁힘이 조용히 넓어지면 그 사실이 사라집니다 —
필드가 하나 더 실릴 뿐 예외도 실패도 안 납니다. 그래서 여기서 소리가 나야 합니다.
"""

import datetime
import uuid

import pytest
from fakes import FakeAdmin, FakeAppUser, FakeSession, Store, install
from fastapi.testclient import TestClient
from pydantic import ValidationError

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.models import ScreeningRecord
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    ScreeningContext,
)
from daengs_backend.orchestration.semantic import build_semantic_router_prompt
from daengs_backend.routers import assistant as assistant_router
from daengs_backend.services import screening_context

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()

NOW = datetime.datetime(2026, 9, 7, 12, 0, tzinfo=datetime.UTC)

#: `daengs_screening/agent.py contract()` 가 실제로 내는 모양의 축소판. **통제 문구와 분포가
#: 들어 있는 채로** 저장되는 것이 사실이라, 좁힘을 재려면 여기에도 들어 있어야 합니다.
FULL_RESULT = {
    "contract_version": "v9-test",
    "verdict": "abnormal",
    "headline": "피부에 이상 소견이 보입니다.",
    "body": "어떤 병변인지는 이 사진만으로 판단할 수 없습니다.",
    "action": "수의사 진료를 받아보시기를 권합니다.",
    "stage1": {"abnormal_prob": 0.956, "abnormal_percent": 95.6, "calibrated": False},
    "stage2": {
        "shown": True,
        "distribution": [{"code": "A1", "name_ko": "구진·플라크", "prob": 0.42}],
        "group": {"name": "융기·발진", "prob": 0.61},
    },
    "disclaimer": "이 결과는 수의학적 진단이 아니며…",
    "meta": {"stage2_low_confidence": True},
}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


def _record(
    *,
    owner: uuid.UUID = OWNER,
    status: str = "DONE",
    result: dict | None = None,
    created_at: datetime.datetime | None = NOW,
) -> ScreeningRecord:
    return ScreeningRecord(
        id=uuid.uuid4(),
        app_user_id=owner,
        status=status,
        photo_storage_key=f"screening/{owner}/{uuid.uuid4()}/photo.jpg",
        photo_content_type="image/jpeg",
        result=FULL_RESULT if result is None else result,
        contract_version="v9-test",
        created_at=created_at,
    )


async def _resolve(record: ScreeningRecord, *, now: datetime.datetime = NOW):
    return await screening_context.resolve(object(), OWNER, record.id, now=now)


# ---------------------------------------------------------------- 좁힘 (D-023)


async def test_판정과_경과일만_넘어간다(store: Store) -> None:
    record = _record()
    store.screenings.append(record)
    assert await _resolve(record) == {"verdict": "abnormal", "days_ago": 0}


async def test_병변_분포와_계열은_안_넘어간다(store: Store) -> None:
    """2단계 병변명이 holdout 에서 56.6% 틀립니다 (D-023). 지금 그 방어가 서 있는 이유는
    이름을 말하는 코드 경로가 **없기** 때문이고, 여기가 그 사실을 지키는 자리입니다."""
    record = _record()
    store.screenings.append(record)
    resolved = await _resolve(record)
    assert resolved is not None
    flat = str(resolved)
    for 금지 in ("distribution", "group", "A1", "구진", "융기"):
        assert 금지 not in flat


async def test_통제_문구는_안_넘어간다(store: Store) -> None:
    """`headline`·`body`·`action`·`disclaimer` 는 사용자에게 무수정으로 나갈 것이지
    모델이 읽을 것이 아닙니다 (#79). 넘기면 지켜야 할 곳이 두 군데가 됩니다."""
    record = _record()
    store.screenings.append(record)
    resolved = await _resolve(record)
    assert set(resolved or {}) == {"verdict", "days_ago"}


async def test_보정_안_된_확률은_안_넘어간다(store: Store) -> None:
    """`stage1.calibrated` 가 false 라 "95.6%" 는 순서지 빈도가 아닙니다 (#79).
    프롬프트로 시키는 대신 값이 없어서 그럴 수 없게 둡니다."""
    record = _record()
    store.screenings.append(record)
    resolved = await _resolve(record)
    assert "0.956" not in str(resolved) and "95.6" not in str(resolved)


def test_계약이_넓어지면_소리가_난다() -> None:
    """좁힘은 `extra="forbid"` 가 강제합니다 — 조용히 한 칸 더 실을 수 없습니다."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScreeningContext.model_validate(
            {"verdict": "abnormal", "days_ago": 0, "stage2": {"shown": True}}
        )


# ---------------------------------------------------------------- 조회


async def test_남의_기록은_없는_것과_같다(store: Store) -> None:
    """소유권은 `screening_repo.get_owned` 가 쿼리 조건으로 묶습니다."""
    alien = _record(owner=STRANGER)
    store.screenings.append(alien)
    assert await screening_context.resolve(object(), OWNER, alien.id, now=NOW) is None


async def test_없는_기록도_오류가_아니다(store: Store) -> None:
    """기록을 못 찾았다는 이유로 답할 수 있는 질문을 실패시키지 않습니다."""
    assert await screening_context.resolve(object(), OWNER, uuid.uuid4(), now=NOW) is None
    assert await screening_context.resolve(object(), OWNER, "uuid 아님", now=NOW) is None


@pytest.mark.parametrize("status", ["PENDING_UPLOAD", "FAILED"])
async def test_판정이_끝나지_않은_기록은_안_쓴다(store: Store, status: str) -> None:
    record = _record(status=status, result=None)
    store.screenings.append(record)
    assert await _resolve(record) is None


async def test_모르는_판정은_아는_척하지_않는다(store: Store) -> None:
    """계약이 늘면 여기서 막히는 것이 맞습니다 — 하류가 모르는 값을 해석해 버립니다."""
    record = _record(result={**FULL_RESULT, "verdict": "inconclusive"})
    store.screenings.append(record)
    assert await _resolve(record) is None


async def test_result_가_없으면_안_쓴다(store: Store) -> None:
    record = _record(result={})
    store.screenings.append(record)
    assert await _resolve(record) is None


# ---------------------------------------------------------------- 경과일


async def test_경과일은_날짜_차이다(store: Store) -> None:
    record = _record(created_at=NOW - datetime.timedelta(days=30, hours=3))
    store.screenings.append(record)
    assert (await _resolve(record))["days_ago"] == 30


async def test_앞날짜는_0이다(store: Store) -> None:
    """시계가 어긋나도 음수 경과를 만들지 않습니다."""
    record = _record(created_at=NOW + datetime.timedelta(days=2))
    store.screenings.append(record)
    assert (await _resolve(record))["days_ago"] == 0


async def test_아주_오래된_기록은_상한에서_잘린다(store: Store) -> None:
    record = _record(created_at=NOW - datetime.timedelta(days=9_000))
    store.screenings.append(record)
    assert (await _resolve(record))["days_ago"] == 3_650


async def test_언제_찍었는지_모르면_안_넘긴다(store: Store) -> None:
    """0 으로 채우면 방금 찍은 것처럼 읽힙니다. 지어내지 않습니다."""
    record = _record(created_at=None)
    store.screenings.append(record)
    assert await _resolve(record) is None


async def test_naive_시각도_UTC로_읽는다(store: Store) -> None:
    """`DateTime(timezone=True)` 컬럼이지만 경로에 따라 naive 로 옵니다."""
    record = _record(created_at=datetime.datetime(2026, 9, 1, 12, 0))  # noqa: DTZ001 — 일부러 naive
    store.screenings.append(record)
    assert (await _resolve(record))["days_ago"] == 6


# ---------------------------------------------------------------- HTTP 배선


class _Factory:
    """`async_sessionmaker` 대역. **연 횟수를 셉니다** — 필드를 안 보낸 요청이 DB 를
    아예 안 여는 성질(D-048 의 무상태 계약)을 이 카드가 깨지 않았는지 보는 자리입니다."""

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
            request_id="test-request",
            status=AssistantStatus.ANSWERED,
            message="답변입니다",
            results=[],
            handoffs=[],
            clarify=None,
        )


@pytest.fixture
def factory() -> _Factory:
    return _Factory()


@pytest.fixture
def service() -> _FakeService:
    return _FakeService()


@pytest.fixture
def client(store: Store, factory: _Factory, service: _FakeService):
    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = (
        lambda: service
    )
    app.dependency_overrides[assistant_router.get_chat_session_factory] = lambda: factory
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _post(client: TestClient, body: dict, subject: uuid.UUID = OWNER):
    token = create_access_token(subject, SubjectType.APP)
    return client.post(
        "/assistant/query", json=body, headers={"Authorization": f"Bearer {token}"}
    )


def test_기록_참조는_컨텍스트로_해석돼_넘어간다(client, store, service) -> None:
    """HTTP 로 오는 것은 id 하나이고, 컨텍스트에 실리는 것은 좁혀진 사실 둘이다.

    시각은 라우터가 `now` 를 안 넘기므로 **실제 시계**로 잰다 — 그래서 기록도 진짜
    과거로 만든다."""
    record = _record(created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=3))
    store.screenings.append(record)
    got = _post(client, {"query": "이럴 때 병원비 지원 있어?",
                         "screening_record_id": str(record.id)})
    assert got.status_code == 200
    assert service.calls[0]["context"]["screening"] == {"verdict": "abnormal", "days_ago": 3}


def test_남의_기록을_지목해도_실패가_아니다(client, store, service) -> None:
    """404 를 주면 "그 기록이 존재한다" 가 새고, 답할 수 있는 질문도 죽습니다."""
    alien = _record(owner=STRANGER)
    store.screenings.append(alien)
    got = _post(client, {"query": "병원비 지원 있어?", "screening_record_id": str(alien.id)})
    assert got.status_code == 200
    assert "screening" not in service.calls[0]["context"]


def test_없는_기록도_실패가_아니다(client, service) -> None:
    """앱이 옛 `/screen/v1/screen` fallback 으로 찍은 건은 애초에 행이 없습니다 (#239)."""
    got = _post(client, {"query": "병원비 지원 있어?",
                         "screening_record_id": str(uuid.uuid4())})
    assert got.status_code == 200
    assert "screening" not in service.calls[0]["context"]


def test_uuid가_아니면_HTTP_경계에서_422다(client, service) -> None:
    got = _post(client, {"query": "병원비 지원 있어?", "screening_record_id": "기록1"})
    assert got.status_code == 422
    assert service.calls == []


def test_안_보내면_DB를_한_번도_안_연다(client, factory, service) -> None:
    """무상태 요청이 DB 를 안 여는 성질(D-048)을 이 카드가 상시로 깨면 안 됩니다."""
    assert _post(client, {"query": "병원비 지원 있어?"}).status_code == 200
    assert factory.opened == 0
    assert "screening" not in service.calls[0]["context"]


def test_판정_본문은_요청으로_못_보낸다(client, service) -> None:
    """`extra="forbid"` — 검증하지 않은 판정이 대화 turn 으로 저장되는 길을 막습니다."""
    got = _post(client, {"query": "병원비 지원 있어?", "screening_result": FULL_RESULT})
    assert got.status_code == 422
    assert service.calls == []


def test_라우터_프롬프트는_기록을_안_본다(store) -> None:
    """승인된 라우팅 메타데이터는 셋뿐입니다 (`semantic._ROUTING_METADATA_KEYS`).
    판정은 라우팅 신호가 아니라 능력이 쓸 사실이라, 프롬프트에 실리면 안 됩니다."""
    prompt = build_semantic_router_prompt(
        query="병원비 지원 있어?",
        context={"screening": {"verdict": "abnormal", "days_ago": 3}},
    )
    assert "abnormal" not in prompt
    assert "screening" not in prompt
