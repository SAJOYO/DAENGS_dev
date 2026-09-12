"""D-073 — 기록된 산책이 비서 프롬프트까지 가는 길, 그리고 **못 잴 때 무엇을 말하는가**.

DB 는 안 씁니다. `test_assistant_care_log.py` 와 같은 꼴로 리포지토리를 가짜로 바꿉니다.
여기서 보는 것은 **규칙**입니다 — 좌표가 한 칸도 안 넘어가는가, 측정 안 된 산책이 합계에
안 섞이는가, 기록이 없는 요청의 프롬프트가 이 카드 전과 글자까지 같은가.
"""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.orchestration.adapters.general import build_general_prompt
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    GeneralPayload,
    WalkActivityContext,
)
from daengs_backend.orchestration.planner import _payload_for, _walk_activity_context
from daengs_backend.orchestration.redirects import DISTANCE_FROM_RECORDED_WALKS_ONLY
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.repositories.walk import WalkActivitySums, _measured_stmt, _walked_stmt
from daengs_backend.routers import assistant as assistant_router
from daengs_backend.services import walk_activity_context


def _compiled(stmt) -> str:
    """리터럴을 박아 컴파일한 SQL, 공백을 한 칸으로 접어 소문자로.

    `inspect.getsource` 대신 이것을 쓴다 — 소스 텍스트 검사는 독스트링까지 함께
    읽어서, 조인 조건을 `WalkAnalysis.walk_id == Walk.id` 로 되돌려도 독스트링에
    `ActivityWalkHead` 한 마디만 남아 있으면 통과해 버린다. 컴파일된 SQL 은
    실제로 어떤 조인이 나가는지를 본다 — DB·픽스처·네트워크 없이 이 venv 안에서
    즉시 만들 수 있다(`select().compile(dialect=...)`).
    """
    compiled = stmt.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    return re.sub(r"\s+", " ", str(compiled)).strip().lower()


def test_disclosure_names_the_record_and_refuses_the_described_route() -> None:
    """D-051 ⑤ 의 고지와 같은 성질 — 못 하는 사실과 **그 이유**를 같이 말한다."""
    assert "기록된 산책" in DISTANCE_FROM_RECORDED_WALKS_ONLY
    assert "말씀" in DISTANCE_FROM_RECORDED_WALKS_ONLY
    # 되묻는 문장으로 읽히면 안 된다 — `VET_CONTACT_LOCATION_UNKNOWN` 과 같은 규칙
    assert "?" not in DISTANCE_FROM_RECORDED_WALKS_ONLY


def test_walk_activity_context_carries_no_coordinate() -> None:
    """좌표는 이 카드에서 한 칸도 안 넘어간다 (Global Constraint 2)."""
    for forbidden in ("lat", "lon", "lng", "polyline", "points", "path"):
        assert forbidden not in WalkActivityContext.model_fields, forbidden


def test_measured_walks_never_exceed_recorded_walks() -> None:
    """측정된 산책이 기록된 산책보다 많을 수 없다 — 조인이 중복 합산하면 여기서 걸린다."""
    with pytest.raises(ValidationError):
        WalkActivityContext(
            day="2026-09-12", walk_count=1, measured_walk_count=2,
            distance_m=100, moving_s=60,
        )


def test_measured_walks_equal_to_recorded_walks_is_allowed() -> None:
    """경계값 — 전부 측정됐으면(`==`) 통과해야 한다.

    검증기는 `measured_walk_count > walk_count` 만 거부한다(`>`). 나중에 이 비교가
    `>=` 로 잘못 바뀌면 이 테스트가 그 자리에서 실패한다 — Task 1 리뷰가 남긴 미결.
    """
    WalkActivityContext(
        day="2026-09-12", walk_count=2, measured_walk_count=2,
        distance_m=1_200, moving_s=900,
    )


def test_general_payload_takes_walk_activity_and_life_does_not() -> None:
    """`care_log` 와 같은 규칙 — 폴백에만 간다."""
    from daengs_backend.orchestration.contracts import LifePayload

    activity = WalkActivityContext(
        day="2026-09-12", walk_count=2, measured_walk_count=1,
        distance_m=1_200, moving_s=900, last_started_at="08:30",
    )
    assert GeneralPayload(question="q", walk_activity=activity).walk_activity == activity
    with pytest.raises(ValidationError):
        LifePayload(question="q", walk_activity=activity)


def test_measured_stmt_joins_through_the_head_never_walk_analyses_directly() -> None:
    """컴파일된 SQL 로 조인 사슬을 본다 — `walk_analyses` 직결을 막는 것이 이 카드의 요점.

    `walk_analyses` 는 (walk_id, fingerprint, 버전 4개) 로 유니크라 한 산책에 여러 세대가
    쌓인다. `activity_walk_heads` 를 경유하지 않고 `WalkAnalysis.walk_id == Walk.id` 로
    바로 이으면 거리가 세대 수만큼 불어나는데, 예외도 경고도 안 난다 — 소스 텍스트 검사로는
    이 실패를 못 잡는다(독스트링에 이름만 한 번 나와도 통과한다). 그래서 실제로 나갈 SQL을
    본다.
    """
    pet_id = uuid.uuid4()
    start = datetime(2026, 9, 12, tzinfo=UTC)
    end = start + timedelta(days=1)

    walked_sql = _compiled(_walked_stmt(pet_id, start, end))
    measured_sql = _compiled(_measured_stmt(pet_id, start, end))

    # 1) activity_walk_heads 를 walks.id 로 잇는 조인이 있다
    assert "join activity_walk_heads on activity_walk_heads.walk_id = walks.id" in measured_sql
    # 2) walk_analyses 를 activity_walk_heads.analysis_id 로 잇는다
    assert "join walk_analyses on walk_analyses.id = activity_walk_heads.analysis_id" in measured_sql
    # 3) walk_analyses.walk_id = walks.id 직결이 없다 — 이 카드가 막으려는 그 실패
    assert "walk_analyses.walk_id = walks.id" not in measured_sql

    # 4) WHERE 절이 동일하다 — 다르면 measured_walk_count 가 walk_count 를 넘어설 수 있고,
    #    그러면 WalkActivityContext 의 검증기가 런타임에 터진다.
    walked_where = walked_sql[walked_sql.index(" where "):]
    measured_where = measured_sql[measured_sql.index(" where "):]
    assert walked_where == measured_where


def test_unmeasured_walks_count_but_do_not_add_distance() -> None:
    """봉인 안 된 산책은 건수에는 들어가고 합계에는 안 들어간다 — 계약이 그 조합을 받아준다."""
    sums = WalkActivitySums(
        walk_count=3, measured_walk_count=2, distance_m=1_200, moving_s=900,
        last_started_at=datetime(2026, 9, 12, 8, 30, tzinfo=UTC),
    )
    # "3건 중 2건만 계산됐어요" 를 말하려면 이 조합이 검증을 통과해야 한다
    WalkActivityContext(
        day="2026-09-12", walk_count=sums.walk_count,
        measured_walk_count=sums.measured_walk_count,
        distance_m=sums.distance_m, moving_s=sums.moving_s,
    )


# ---------------------------------------------------------------- Task 3: resolver
#
# 아래는 `services.walk_activity_context.resolve` 를 본다. DB 는 안 쓴다 — 강아지는
# `fakes.install` 이 바꿔치기한 `pet_repo` 로 오고, `walk_repo.activity_for_pet_between` 은
# 이 파일이 직접 monkeypatch 한다 (`test_assistant_care_log.py` 의 `care` 와 같은 꼴).

OWNER = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
STRANGER = uuid.uuid4()
TODAY = date(2026, 9, 12)
SEOUL = ZoneInfo("Asia/Seoul")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def pet(store: Store) -> FakePet:
    p = FakePet(app_user_id=OWNER, name="네옹", breed="dog_pug")
    store.pets.append(p)
    return p


@pytest.fixture
def walks(monkeypatch: pytest.MonkeyPatch) -> dict[str, WalkActivitySums]:
    """`activity_for_pet_between` 을 가짜로 바꾼다 — care_log 테스트의 `care` 와 같은 꼴."""
    box = {"sums": WalkActivitySums(0, 0, 0, 0, None)}

    async def activity_for_pet_between(session, pet_id, start, end):
        return box["sums"]

    monkeypatch.setattr(walk_repo, "activity_for_pet_between", activity_for_pet_between)
    return box


async def _resolve(pet_id, *, today: date = TODAY):
    return await walk_activity_context.resolve(object(), OWNER, str(pet_id), today=today)


async def test_기록이_없는_날은_None(pet, walks) -> None:
    """빈 날은 "안 걸었다" 가 아니라 "산책 기록을 안 쓴다" 일 수 있다."""
    assert await _resolve(pet.id) is None


async def test_uuid_가_아니면_None(walks) -> None:
    assert await _resolve("uuid 아님") is None


async def test_남의_강아지는_None(store, walks) -> None:
    """`activity_for_pet_between` 은 소유자 조건을 안 건다 — 걸러 주는 것은 resolver 뿐이다.

    브리프 원문은 `walks` 만 받고 등록 안 된 임의 uuid 를 넘겼는데, 그러면 기본 `walks`
    상자(건수 0)만으로도 "빈 날" 분기에 걸려 통과해 버려 소유권 검사를 실제로는 안 본다.
    여기서는 **남의 강아지에 건수가 있어도** None 이어야 한다는 것을 검증한다 — 그래야
    `resolve` 가 소유권을 실제로 확인한다는 것이 증명된다.
    """
    alien = FakePet(app_user_id=STRANGER, name="남의개", breed="dog_pug")
    store.pets.append(alien)
    walks["sums"] = WalkActivitySums(3, 2, 1_200, 900, None)
    assert await _resolve(alien.id) is None


async def test_DB_오류는_삼키고_None(pet, walks, monkeypatch, caplog) -> None:
    """`walk_analyses`·`activity_walk_heads` 가 서버에 아직 없을 수 있다. 그때 비서가
    죽으면 안 된다 — 기록 없이, 이 카드 전과 똑같이 답한다."""

    async def boom(*args, **kwargs):
        raise OperationalError(
            "SELECT", {}, Exception('relation "activity_walk_heads" does not exist')
        )

    monkeypatch.setattr(walk_repo, "activity_for_pet_between", boom)
    with caplog.at_level("WARNING"):
        assert await _resolve(pet.id) is None
    assert "산책" in caplog.text


async def test_하루를_건수와_합계와_시각으로_좁힌다(pet, walks) -> None:
    """넘어가는 것은 합계와 `HH:MM` 뿐 — 타임스탬프도 좌표도 아니다."""
    walks["sums"] = WalkActivitySums(
        walk_count=3, measured_walk_count=2, distance_m=1_240, moving_s=1_500,
        last_started_at=datetime(2026, 9, 12, 8, 30, tzinfo=SEOUL),
    )
    assert await _resolve(pet.id) == {
        "day": "2026-09-12",
        "walk_count": 3,
        "measured_walk_count": 2,
        "distance_m": 1_240,
        "moving_s": 1_500,
        "last_started_at": "08:30",
    }


async def test_하루_경계는_서울_자정이고_tz_aware(pet, walks, monkeypatch) -> None:
    """`walks.started_at` 은 timestamptz 다. naive 로 넘기면 Postgres 가 서버 TZ 로
    해석해 하루 경계가 밀린다 — 예외도 경고도 안 난다. 케어 로그와 같은 "오늘" 이어야
    두 요약이 한 프롬프트에서 서로 다른 하루를 말하지 않는다 (`care_service.DAY_TIMEZONE`)."""
    seen: dict[str, object] = {}

    async def activity_for_pet_between(session, pet_id, start, end):
        seen["start"] = start
        seen["end"] = end
        return WalkActivitySums(0, 0, 0, 0, None)

    monkeypatch.setattr(walk_repo, "activity_for_pet_between", activity_for_pet_between)

    await _resolve(pet.id)

    assert seen["start"] == datetime(2026, 9, 12, 0, 0, tzinfo=SEOUL)
    assert seen["end"] == datetime(2026, 9, 13, 0, 0, tzinfo=SEOUL)
    assert seen["start"].tzinfo is not None
    assert seen["end"].tzinfo is not None


# ---------------------------------------------------------------- Task 4: planner 배선
#
# `_walk_activity_context` 는 `_care_log_context` 의 화이트리스트 복사기와 같은 자리다 —
# 모양이 틀린 칸은 그 칸만 버리고 요청은 안 버린다.


def test_planner_drops_a_malformed_field_not_the_request() -> None:
    """`_care_log_context` 와 같은 규칙 — 모양이 틀린 칸은 그 칸만 버린다."""
    assert _walk_activity_context({"walk_activity": "not a mapping"}) is None
    assert _walk_activity_context({}) is None
    resolved = _walk_activity_context({
        "walk_activity": {
            "day": "2026-09-12", "walk_count": 2, "measured_walk_count": 1,
            "distance_m": 1_200, "moving_s": 900, "last_started_at": "8:30",  # 잘못된 시각
        }
    })
    assert resolved is not None
    assert "last_started_at" not in resolved  # 그 칸만 빠진다
    assert resolved["walk_count"] == 2
    assert resolved["measured_walk_count"] == 1
    assert resolved["distance_m"] == 1_200
    assert resolved["moving_s"] == 900


def test_planner_rejects_the_whole_block_when_a_required_field_is_malformed() -> None:
    """`last_started_at` 만 선택이다 — 나머지 필수 칸이 틀리면 그 칸만이 아니라 블록 전체가
    빠진다. `_care_log_context` 가 건수를 하나도 못 읽으면 블록째 버리는 것과 같은 자리다."""
    assert _walk_activity_context({"walk_activity": {
        "day": "2026-09-12", "walk_count": "두 번", "measured_walk_count": 1,
        "distance_m": 1_200, "moving_s": 900,
    }}) is None
    # measured_walk_count 가 walk_count 를 넘는 조합도 통째로 버린다 —
    # `WalkActivityContext` 의 검증기가 런타임에 터지게 두지 않는다.
    assert _walk_activity_context({"walk_activity": {
        "day": "2026-09-12", "walk_count": 1, "measured_walk_count": 2,
        "distance_m": 1_200, "moving_s": 900,
    }}) is None


def test_life_never_receives_the_walk_summary() -> None:
    """폴백에만 간다 — `care_log` 와 같다. planner 가 Life payload 에 안 얹는지 본다."""
    context = {
        "walk_activity": {
            "day": "2026-09-12", "walk_count": 1, "measured_walk_count": 1,
            "distance_m": 1_200, "moving_s": 900,
        }
    }
    # `_payload_for` 는 capability 뒤가 전부 키워드 전용이다 (planner.py:272)
    assert "walk_activity" not in _payload_for("life", query="q", context=context)
    assert "walk_activity" in _payload_for("general", query="q", context=context)


def test_a_request_without_a_record_builds_the_same_prompt_as_before() -> None:
    """**이 카드의 가장 중요한 테스트.** 기록이 없는 요청의 프롬프트가 바이트로 같아야 한다.

    `tests/test_assistant_care_log.py` 가 v3 본문에 대해 하는 것과 같은 자리다 —
    D-057 ③ 의 승인 계보가 여기 걸려 있다 (Global Constraint 4).

    **이 태스크 시점에서 이 단언은 아무것도 증명하지 않는다** — `build_general_prompt`
    는 아직 `walk_activity` 를 읽지 않으므로(다음 태스크 몫), payload 에 값을 실어도
    `WALK_ACTIVITY` 문자열이 나올 자리 자체가 코드에 없다. 지금 통과하는 이유는
    "배선이 안전하다" 가 아니라 "아직 배선이 없다" 다. 그래도 지금 넣어 두는 것은
    다음 태스크가 그 블록을 더할 때 이 테스트가 지킴이가 되기 때문이다.
    """
    payload = GeneralPayload(question="밥은 하루에 몇 번 줘야 해?")
    assert "WALK_ACTIVITY" not in build_general_prompt(payload)


# ---------------------------------------------------------------- Task 4: HTTP 배선


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
def client(store: Store, walks, factory: _Factory, service: _FakeService):
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


def test_활성_강아지가_있으면_오늘_산책_요약이_컨텍스트에_실린다(client, pet, walks, factory, service) -> None:
    """라우터는 `today` 를 안 넘기므로 **실제 시계**로 잰다 — 그래서 기록도 지금 걸로 만든다.

    세션은 **프로필·케어 로그·진료비 조회와 같은 하나**다(`_with_dog_context`). 산책 요약을
    위해 세션을 하나 더 열면 그 함수가 막으려는 비용이 그대로 든다."""
    walks["sums"] = WalkActivitySums(
        walk_count=2, measured_walk_count=1, distance_m=1_200, moving_s=900,
        last_started_at=None,
    )
    got = _post(client, {"query": "오늘 얼마나 걸었어?", "active_dog_id": str(pet.id)})
    assert got.status_code == 200
    assert factory.opened == 1
    context = service.calls[0]["context"]
    assert context["walk_activity"]["walk_count"] == 2
    assert context["walk_activity"]["measured_walk_count"] == 1
    assert context["walk_activity"]["distance_m"] == 1_200
    assert "lat" not in context["walk_activity"] and "lon" not in context["walk_activity"]


def test_기록이_없으면_컨텍스트에_안_실린다(client, pet, walks, service) -> None:
    got = _post(client, {"query": "오늘 얼마나 걸었어?", "active_dog_id": str(pet.id)})
    assert got.status_code == 200
    assert "walk_activity" not in service.calls[0]["context"]


# ---------------------------------------------------------------- Task 5: 프롬프트 블록과
# `-walk` 접미사
#
# 브리프의 기대값 `general-answer-ko-v8-walk` 는 낡았다 — Task 6(마커)이 이미 병합돼
# `GeneralAnswer` 의 JSON 스키마가 커졌고, 그 스키마가 프롬프트에 그대로 박히므로 base
# 버전이 v8 에서 v9 로 올랐다. 여기서는 v9 를 쓴다.


def test_walk_suffix_sits_between_the_base_and_conv() -> None:
    """순서를 고정한다 — `<base>` → `-walk` → `-conv` (Global Constraint 5).

    `conversation` 없이 `-walk` 만 확인하면 이름이 약속한 "`-conv` **앞**" 을 실제로는
    안 잰다 — `conversation` 을 같이 실어 `-walk-conv` 순서까지 단언한다.
    """
    from daengs_backend.orchestration.adapters.general import general_prompt_version
    from daengs_backend.orchestration.contracts import ConversationContext, TurnRelation

    activity = WalkActivityContext(
        day="2026-09-12", walk_count=1, measured_walk_count=1,
        distance_m=1_200, moving_s=900,
    )
    assert general_prompt_version(
        GeneralPayload(question="q", walk_activity=activity)
    ) == "general-answer-ko-v10-walk"
    assert general_prompt_version(
        GeneralPayload(
            question="q", walk_activity=activity,
            conversation=ConversationContext(relation=TurnRelation.NEW),
        )
    ) == "general-answer-ko-v10-walk-conv"


def test_the_rule_forbids_estimating_from_a_described_route() -> None:
    """D-051 을 프롬프트에서 한 번 더 못 박는다 (Global Constraint 1)."""
    activity = WalkActivityContext(
        day="2026-09-12", walk_count=1, measured_walk_count=1,
        distance_m=1_200, moving_s=900,
    )
    prompt = build_general_prompt(GeneralPayload(question="q", walk_activity=activity))
    assert "WALK_ACTIVITY" in prompt
    assert "Never estimate the distance or the time from a route described in words" in prompt
    assert "from place names" in prompt


def test_the_rule_makes_the_two_counts_speakable() -> None:
    """측정 안 된 산책이 있으면 답이 그 사실을 말할 수 있어야 한다."""
    activity = WalkActivityContext(
        day="2026-09-12", walk_count=3, measured_walk_count=2,
        distance_m=1_240, moving_s=1_500,
    )
    prompt = build_general_prompt(GeneralPayload(question="q", walk_activity=activity))
    assert "walk_count is larger than measured_walk_count" in prompt


def test_conversation_block_still_sits_immediately_before_user_query() -> None:
    """`walk_activity` 와 `conversation` 이 함께 있어도 CONVERSATION 은 USER_QUERY 바로 앞이다.

    `a < b < c` 는 순서만 잰다 — 사이에 다른 블록이 끼어들어도 통과해 버린다. 여기서
    지키려는 것은 "WALK_ACTIVITY 줄과 USER_QUERY 줄 사이에는 CONVERSATION 블록 말고는
    아무것도 없다" 이므로, 그 사이 텍스트를 `render_conversation_context` 가 내는 값과
    글자 단위로 비교한다.
    """
    from daengs_backend.orchestration.contracts import ConversationContext, TurnRelation
    from daengs_backend.orchestration.semantic import render_conversation_context

    activity = WalkActivityContext(
        day="2026-09-12", walk_count=1, measured_walk_count=1,
        distance_m=1_200, moving_s=900,
    )
    conversation = ConversationContext(relation=TurnRelation.NEW)
    prompt = build_general_prompt(
        GeneralPayload(question="q", walk_activity=activity, conversation=conversation)
    )
    walk_idx = prompt.index("WALK_ACTIVITY:")
    user_query_idx = prompt.index("USER_QUERY:")
    walk_line_end = prompt.index("\n", walk_idx) + 1
    between = prompt[walk_line_end:user_query_idx]
    assert between == render_conversation_context(conversation) + "\n"


# ---------------------------------------------------------------- Task 8: unmeasured 규칙은
# 기록이 0건(`walk_activity is None`)이어도 항상 붙는다 — 이 카드의 존재 이유.


def test_기록이_0건이어도_unmeasured_지시문이_붙는다() -> None:
    """이 카드가 메우는 구멍 — 지금까지는 `_WALK_ACTIVITY_RULE` 이 `payload.walk_activity
    is not None` 일 때만 실려서, 오늘 산책 기록이 하나도 없는 요청(`resolve` 가 `None`
    을 낸 바로 그 경우)은 모델이 `unmeasured` 를 세울 이유를 프롬프트에서 아예 못 받았다.
    `_UNMEASURED_RULE` 은 데이터 유무와 무관하게 항상 붙어야 한다 — 이 테스트가 실패하는
    것을 먼저 본 뒤 구현한다."""
    prompt = build_general_prompt(GeneralPayload(question="나루 얼마나 걸었어?"))
    assert "WALK_ACTIVITY:" not in prompt
    # 스키마 블록 자체에는 이 문장이 없다 — `unmeasured` 필드에 `description` 을 안 달아서다.
    assert "Never estimate the distance or the time from a route described in words" in prompt
    assert "from place names" in prompt
    # D-051 을 지키는 핵심 문장 — 말로 설명한 경로/지명으로 추정하지 말라는 것.
    assert "do not tell the owner to use a map app" in prompt
