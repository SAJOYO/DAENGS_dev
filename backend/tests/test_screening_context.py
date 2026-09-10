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
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from fastapi.testclient import TestClient
from pydantic import ValidationError

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.models import ScreeningRecord
from daengs_backend.orchestration.contracts import (
    SCREENING_HISTORY_LIMIT,
    AssistantResponse,
    AssistantStatus,
    ScreeningContext,
    ScreeningHistory,
)
from daengs_backend.orchestration.semantic import build_semantic_router_prompt
from daengs_backend.routers import assistant as assistant_router
from daengs_backend.services import screening_context

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
#: 돌보미. 대표의 강아지에 붙은 기록을 컨텍스트로도 볼 수 있어야 한다(Task 14, docs/co-care.md §2).
CARER = uuid.uuid4()

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
    pet_id: uuid.UUID | None = None,
) -> ScreeningRecord:
    return ScreeningRecord(
        id=uuid.uuid4(),
        app_user_id=owner,
        pet_id=pet_id,
        status=status,
        photo_storage_key=f"screening/{owner}/{uuid.uuid4()}/photo.jpg",
        photo_content_type="image/jpeg",
        result=FULL_RESULT if result is None else result,
        contract_version="v9-test",
        created_at=created_at,
    )


async def _resolve(record: ScreeningRecord, *, now: datetime.datetime = NOW):
    return await screening_context.resolve(object(), OWNER, record.id, now=now)


async def _resolve_context(record: ScreeningRecord, *, now: datetime.datetime = NOW):
    return await screening_context.resolve_context(object(), OWNER, record.id, now=now)


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
    """접근은 `screening_repo.get_accessible` 이 쿼리 조건으로 묶습니다 — 창작자도 아니고
    그 기록에 붙은 아이의 구성원도 아니면 없는 것과 같습니다."""
    alien = _record(owner=STRANGER)
    store.screenings.append(alien)
    assert await screening_context.resolve(object(), OWNER, alien.id, now=NOW) is None


async def test_돌보미가_대표의_강아지_기록을_컨텍스트로_본다(store: Store) -> None:
    """`_accessible` 이 `_owned` 를 대체한 이유 — Task 14, docs/co-care.md §2.

    돌보미가 `GET /app/screening/records/{id}` 로 이미 볼 수 있는 기록이라면, 그 기록을
    짚어 물었을 때 어시스턴트 컨텍스트도 같은 것을 봐야 한다. 좁힌 계약을 실제로 통과시켜
    확인한다 — `resolve` 가 `_narrowed`(D-023 좁힘)까지 지나야 값이 나온다.
    """
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    store.pet_members.append((pet.id, CARER))
    record = _record(pet_id=pet.id)
    store.screenings.append(record)

    resolved = await screening_context.resolve(object(), CARER, record.id, now=NOW)
    assert resolved == {"verdict": "abnormal", "days_ago": 0}


async def test_돌보미도_개인_기록은_컨텍스트에서_못_본다(store: Store) -> None:
    """`pet_id IS NULL` 인 개인 기록은 강아지가 없어 구성원이라는 개념이 안 걸린다 —
    같은 강아지를 함께 돌보는 사이여도 예외가 없다."""
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    store.pet_members.append((pet.id, CARER))
    personal = _record(pet_id=None)  # OWNER 의 개인 기록
    store.screenings.append(personal)

    assert await screening_context.resolve(object(), CARER, personal.id, now=NOW) is None


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


# ---------------------------------------------------------------- 이력 (#79 3번)
#
# 이력의 진입 신호는 `screening_record_id` 입니다 — "지난번보다 어때요" 에는 앱이 보낼
# 참조가 따로 없어서, 결과 화면에서 이어 묻는 그 자리에 얹습니다. 그래서 이 절의 테스트는
# 전부 "기준 기록 하나를 지목했을 때 **그 아이의 이전 기록**이 어디까지 따라오는가" 입니다.


PET = uuid.uuid4()
OTHER_PET = uuid.uuid4()


def _pet_record(days_ago: int, *, verdict: str = "normal", **kwargs) -> ScreeningRecord:
    """`PET` 의 `DONE` 기록 한 건. 날짜만 다르게 여러 건을 만들 때 씁니다."""
    kwargs.setdefault("pet_id", PET)
    kwargs.setdefault("result", {**FULL_RESULT, "verdict": verdict})
    return _record(created_at=NOW - datetime.timedelta(days=days_ago), **kwargs)


def _fill(store: Store, *records: ScreeningRecord) -> None:
    """순서는 상관없습니다 — fakes 가 `created_at` 으로 실제로 정렬합니다.

    예전에는 "옛것부터 넣어야" 했습니다(fakes 가 담은 순서를 뒤집어 흉내 냈습니다). 그 흉내가
    **담은 순서 = 시간 순서인 테스트만** 통과시켰고, 옛 기록을 기준으로 묻는 갈래가 통째로 안
    돌아 순서 버그를 못 잡았습니다 (#79 3번 리뷰).
    """
    store.screenings.extend(records)


async def test_이전_기록은_최근순으로_넘어간다(store: Store) -> None:
    older = _pet_record(40, verdict="normal")
    newer = _pet_record(10, verdict="retake")
    current = _pet_record(0, verdict="abnormal")
    _fill(store, older, newer, current)
    assert await _resolve_context(current) == {
        "screening": {"verdict": "abnormal", "days_ago": 0},
        "screening_history": [
            {"verdict": "retake", "days_ago": 10},
            {"verdict": "normal", "days_ago": 40},
        ],
    }


async def test_기준보다_나중_기록은_이력이_아니다(store: Store) -> None:
    """**앱의 기록 화면에서 옛 기록을 열고 묻는 흐름입니다.** `screening_record_id` 는 소유자
    것이면 아무거나 받으므로(#307) 기준이 최신이라는 보장이 없습니다. 나중 기록을 "지난번"
    으로 실으면 사용자가 읽는 시간 순서가 거꾸로입니다.
    """
    old_reference = _pet_record(40, verdict="normal")
    _fill(
        store,
        old_reference,
        _pet_record(10, verdict="retake"),
        _pet_record(0, verdict="abnormal"),
    )
    resolved = await _resolve_context(old_reference)
    assert resolved["screening"] == {"verdict": "normal", "days_ago": 40}
    assert "screening_history" not in resolved


async def test_기준이_스캔_창_밖이어도_이전_기록을_찾는다(store: Store) -> None:
    """`limit` 은 **거르기 전에** 걸립니다. 기준보다 나중 기록이 스캔 창을 다 채우면, 부르는
    쪽에서 아무리 걸러도 이력이 빈 채로 나옵니다 — 그래서 조건이 쿼리에 있어야 합니다.
    """
    reference = _pet_record(100, verdict="abnormal")
    earlier = _pet_record(200, verdict="normal")
    # 기준보다 나중 기록으로 `_HISTORY_SCAN_LIMIT`(12) 을 넘긴다.
    _fill(store, earlier, reference, *(_pet_record(day) for day in range(1, 15)))
    resolved = await _resolve_context(reference)
    assert resolved["screening_history"] == [{"verdict": "normal", "days_ago": 200}]


async def test_같은_시각이면_id_로_가른다(store: Store) -> None:
    """시각이 같은 두 행의 순서가 실행마다 달라지면 같은 질문이 다른 이력을 받습니다."""
    same = NOW - datetime.timedelta(days=5)
    first, second = sorted(
        (_pet_record(5, verdict="normal"), _pet_record(5, verdict="retake")),
        key=lambda r: r.id,
    )
    first.created_at = second.created_at = same
    _fill(store, first, second)
    # 뒤엣것(id 가 큰 쪽)을 기준으로 하면 앞엣것만 이력이다. 그 반대는 이력이 없다.
    assert (await _resolve_context(second))["screening_history"] == [
        {"verdict": first.result["verdict"], "days_ago": 5}
    ]
    assert "screening_history" not in await _resolve_context(first)


async def test_기준_기록_자신은_이력에_없다(store: Store) -> None:
    """이미 `screening` 으로 가 있습니다. 두 번 실리면 '기록이 두 건' 으로 읽힙니다."""
    current = _pet_record(0)
    _fill(store, current)
    assert await _resolve_context(current) == {
        "screening": {"verdict": "normal", "days_ago": 0}
    }


async def test_첫_기록이면_이력_키가_아예_없다(store: Store) -> None:
    """빈 목록을 실어 '이력을 봤는데 없더라' 를 말하지 않습니다 — 안 실으면 이 기능
    이전과 똑같은 컨텍스트입니다."""
    current = _pet_record(0)
    _fill(store, current)
    assert "screening_history" not in await _resolve_context(current)


async def test_상한을_넘으면_최근_것부터_자른다(store: Store) -> None:
    """상한은 계약에 있습니다 (`SCREENING_HISTORY_LIMIT`). 오래 쓴 아이일수록 한 요청이
    비싸지는 것도, 저장되는 대화 turn 이 길어지는 것도 여기서 멈춥니다."""
    olds = [_pet_record(days) for days in (90, 60, 30, 20, 10)]  # 옛것부터
    current = _pet_record(0)
    _fill(store, *olds, current)
    history = (await _resolve_context(current))["screening_history"]
    assert len(history) == SCREENING_HISTORY_LIMIT
    assert [entry["days_ago"] for entry in history] == [10, 20, 30]


@pytest.mark.parametrize("status", ["PENDING_UPLOAD", "FAILED"])
async def test_판정이_끝나지_않은_기록은_이력에도_없다(store: Store, status: str) -> None:
    """`FAILED` 의 `failure_reason` 은 운영자용이고, 판정 전은 애초에 말할 것이 없습니다."""
    dropped = _pet_record(20, status=status, result=None)
    kept = _pet_record(30)
    current = _pet_record(0)
    _fill(store, kept, dropped, current)
    assert (await _resolve_context(current))["screening_history"] == [
        {"verdict": "normal", "days_ago": 30}
    ]


async def test_못_쓸_행을_건너뛰고도_상한까지_채운다(store: Store) -> None:
    """딱 3건만 읽으면 실패가 몇 번 낀 아이의 이력이 통째로 비어 보입니다."""
    rows: list[ScreeningRecord] = []
    for days in (70, 60, 50, 40, 30, 20):
        rows.append(_pet_record(days + 1, status="FAILED", result=None))
        rows.append(_pet_record(days))
    current = _pet_record(0)
    _fill(store, *rows, current)
    history = (await _resolve_context(current))["screening_history"]
    assert [entry["days_ago"] for entry in history] == [20, 30, 40]


async def test_다른_아이의_기록은_안_섞인다(store: Store) -> None:
    """'지난번' 이 다른 아이의 판정이면 그건 이력이 아니라 오답입니다."""
    sibling = _pet_record(10, pet_id=OTHER_PET, verdict="abnormal")
    mine = _pet_record(20)
    current = _pet_record(0)
    _fill(store, mine, sibling, current)
    assert (await _resolve_context(current))["screening_history"] == [
        {"verdict": "normal", "days_ago": 20}
    ]


async def test_아이를_모르면_이력이_없다(store: Store) -> None:
    """`pet_id` 는 NULL 일 수 있습니다 — 아이를 지우면 FK 가 SET NULL 이고 기록은 남습니다.
    그때 소유자 전체로 넓히면 다른 아이의 판정이 '지난번' 으로 섞입니다."""
    orphan_current = _record(created_at=NOW)
    earlier = _pet_record(10)
    _fill(store, earlier, orphan_current)
    assert await _resolve_context(orphan_current) == {
        "screening": {"verdict": "abnormal", "days_ago": 0}
    }


async def test_남의_기록을_지목하면_이력도_없다(store: Store) -> None:
    """아이를 알 방법이 그 기록뿐이라, 못 읽으면 이력의 진입 자체가 없습니다."""
    alien = _record(owner=STRANGER, pet_id=PET)
    mine = _pet_record(10)
    _fill(store, mine, alien)
    assert await screening_context.resolve_context(object(), OWNER, alien.id, now=NOW) == {}


async def test_기준_기록이_실패해도_이력은_간다(store: Store) -> None:
    """방금 찍은 판정이 실패한 자리에서 '지난번엔 어땠지' 는 그대로 유효한 질문입니다."""
    earlier = _pet_record(10, verdict="abnormal")
    failed_now = _pet_record(0, status="FAILED", result=None)
    _fill(store, earlier, failed_now)
    assert await _resolve_context(failed_now) == {
        "screening_history": [{"verdict": "abnormal", "days_ago": 10}]
    }


async def test_이력에도_분포와_문구와_확률은_안_실린다(store: Store) -> None:
    """좁힘은 건수와 무관합니다 — 이력이라고 병변명이 필요해지지 않습니다 (D-023).
    `ScreeningHistory.entries` 가 `ScreeningContext` 자체인 이유가 이것입니다."""
    earlier = _pet_record(10)
    current = _pet_record(0)
    _fill(store, earlier, current)
    flat = str(await _resolve_context(current))
    for 금지 in ("distribution", "group", "A1", "구진", "융기", "0.956", "95.6",
               "headline", "수의사 진료", "진단이 아니"):
        assert 금지 not in flat


# ---------------------------------------------------------------- 이력 계약


def test_이력_계약이_상한을_강제한다() -> None:
    """상한이 코드의 `break` 에만 있으면 그 줄이 사라질 때 아무것도 안 깨집니다."""
    entries = [
        {"verdict": "normal", "days_ago": day} for day in range(SCREENING_HISTORY_LIMIT)
    ]
    assert len(ScreeningHistory(entries=entries).entries) == SCREENING_HISTORY_LIMIT
    with pytest.raises(ValidationError):
        ScreeningHistory(entries=[*entries, {"verdict": "normal", "days_ago": 99}])


def test_이력_계약도_넓어지면_소리가_난다() -> None:
    """항목이 `ScreeningContext` 라 좁힘이 그대로 걸리고, 감싼 쪽도 `extra="forbid"` 입니다."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScreeningHistory(entries=[{"verdict": "normal", "days_ago": 0, "stage2": {}}])
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScreeningHistory.model_validate({"entries": [], "trend": "improving"})


def test_이력에_추세를_담을_칸이_없다() -> None:
    """두 시점의 차이는 강아지의 변화가 아니라 모델의 잡음일 수 있습니다 (D-023 —
    2단계 병변명 holdout 오답 56.6%, `stage1` 은 보정 전). 비교를 프롬프트로 금지하는
    대신 **비교할 데이터를 안 둡니다.**"""
    assert set(ScreeningHistory.model_fields) == {"entries"}
    for 금지 in ("trend", "improved", "worsened", "delta", "change", "compared_to"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ScreeningHistory.model_validate({"entries": [], 금지: True})


# ---------------------------------------------------------------- 이력 HTTP 배선


def test_이력도_같은_요청에서_컨텍스트로_넘어간다(client, store, service) -> None:
    """진입 신호는 `screening_record_id` 하나입니다 — 이력용 필드를 새로 만들지 않습니다.

    라우터는 `now` 를 안 넘기므로 실제 시계로 잽니다. 기록도 진짜 과거로 만듭니다."""
    real_now = datetime.datetime.now(datetime.UTC)
    earlier = _record(
        pet_id=PET,
        result={**FULL_RESULT, "verdict": "normal"},
        created_at=real_now - datetime.timedelta(days=30),
    )
    current = _record(pet_id=PET, created_at=real_now - datetime.timedelta(days=3))
    _fill(store, earlier, current)
    got = _post(client, {"query": "지난번보다 어때?", "screening_record_id": str(current.id)})
    assert got.status_code == 200
    context = service.calls[0]["context"]
    assert context["screening"] == {"verdict": "abnormal", "days_ago": 3}
    assert context["screening_history"] == [{"verdict": "normal", "days_ago": 30}]


def test_이력_때문에_세션이_더_열리지는_않는다(client, store, service, factory) -> None:
    """이력은 기존 조회에 얹힙니다 — 같은 세션에서 쿼리 하나가 늘 뿐입니다."""
    now = datetime.datetime.now(datetime.UTC)
    earlier = _record(pet_id=PET, created_at=now - datetime.timedelta(days=30))
    current = _record(pet_id=PET, created_at=now)
    _fill(store, earlier, current)
    got = _post(client, {"query": "지난번보다 어때?", "screening_record_id": str(current.id)})
    assert got.status_code == 200
    assert factory.opened == 1


def test_활성_강아지만으로는_이력을_안_읽는다(client, store, factory, service) -> None:
    """진입 신호는 `screening_record_id` 뿐입니다 — `active_dog_id` 는 아직 이력을 안 켭니다.

    일반 대화 전반의 피부 기억으로 넓히는 것은 실제 수요가 확인된 뒤입니다. 열린 세션
    하나는 **프로필 조회**(`_with_dog_context`) 의 것이고, 이력은 거기에 아무것도 안
    더했습니다 — 이 숫자가 2가 되면 넓힌 것입니다."""
    _fill(store, _pet_record(10), _pet_record(3))
    got = _post(client, {"query": "산책 가도 돼?", "active_dog_id": str(PET)})
    assert got.status_code == 200
    assert factory.opened == 1
    assert "screening_history" not in service.calls[0]["context"]
    assert "screening" not in service.calls[0]["context"]


def test_라우터_프롬프트는_이력도_안_본다(store) -> None:
    """승인된 라우팅 메타데이터는 셋뿐입니다 (`semantic._ROUTING_METADATA_KEYS`).
    이력은 라우팅 신호가 아니라 능력이 쓸 사실입니다."""
    prompt = build_semantic_router_prompt(
        query="지난번보다 어때?",
        context={"screening_history": [{"verdict": "abnormal", "days_ago": 30}]},
    )
    assert "abnormal" not in prompt
    assert "screening_history" not in prompt
