"""채팅에서 케어 기록을 남기는 길 — 그리고 **무엇이 절대 안 써지는가** (#331 후속, D-075).

읽기 쪽(#344)은 `test_assistant_care_log.py` 가 봅니다. 여기는 반대 방향입니다:
`"방금 밥 먹였어"` 가 확인 되묻기가 되고, 다음 턴의 `"네"` 가 `care_events` 한 줄이 되는 길.

DB 는 안 씁니다 — `test_care_events.py` 와 같은 꼴로 `care_repo`·`walk_repo` 를 가짜로
바꿔치기합니다. 여기서 보는 것은 **규칙**이고, 규칙의 절반은 "안 쓴다" 입니다:

- 질문·걱정·긴 발화는 기록 진술이 아니다 (`"밥 먹였는데 계속 낑낑거려"` 가 대표 사례)
- 종류가 애매하거나 강아지를 모르거나 플래그가 꺼졌으면 **기록 화면 HANDOFF**
- 승낙이 아닌 말에는 아무 일도 안 일어난다 — 거절은 문구로, 무관한 발화는 평소 라우팅으로
- 묵은 제안(`PROPOSAL_TTL`)에 뒤늦게 "네" 해도 안 쓴다
- 약 중복 창은 채팅이 못 건너뛴다
- 모델은 이 경로에서 **한 번도** 안 불린다

마지막 것이 이 파일에서 제일 중요합니다 — 쓰기 경로에 모델이 끼면, 프롬프트 회귀 하나가
남의 강아지 기록에 행을 남길 수 있게 됩니다.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from sqlalchemy.exc import OperationalError

from daengs_backend.orchestration import care_log as gate
from daengs_backend.orchestration import planner
from daengs_backend.orchestration.adapters.care_log import CareLogCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityStatus,
    CareLogKind,
    CareLogProposal,
    ClarifyRequest,
    RouterKind,
)
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.repositories import care_event as care_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.schemas.care_event import CareEventKind
from daengs_backend.services import care_event as care_service

OWNER = uuid.uuid4()
SEOUL = ZoneInfo("Asia/Seoul")
#: 제안 시각. 서울 오후로 고정 — 확인 문장의 `HH:MM` 이 시간대에 따라 흔들리면 안 됩니다.
NOW = datetime(2026, 9, 13, 14, 32, tzinfo=SEOUL)


# ── 결정론 게이트 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "방금 밥 먹였어",
        "밥 줬어",
        "아침 사료 먹였어",
        "약 먹였어",
        "간식 줬어",
        "저녁 급여 완료",
    ],
)
def test_record_statements_are_recognized(query: str) -> None:
    assert gate.is_care_log_statement(query) is True


@pytest.mark.parametrize(
    ("query", "why"),
    [
        ("밥 언제 줘야 해?", "질문이다"),
        ("사료 하루에 몇 번 줘야 하나요", "질문이다 — 물음표가 없어도"),
        ("밥 먹였는데 계속 낑낑거려", "기록이 아니라 걱정이다"),
        ("약 먹였는데 토했어", "같은 이유 — 그리고 이쪽은 응급 경계가 먼저 본다"),
        ("밥 안 먹였어", "안 한 것은 기록할 것이 없다"),
        ("밥 먹였어 그런데 산책은 지금 가도 괜찮을까?", "섞인 발화다"),
        ("오늘 밥 잘 먹었나 궁금한데 사료를 바꿔볼까 싶기도 하고 양도 좀 줄여야 할까", "길다"),
        ("산책 갔다 왔어", "산책은 walks 가 진실이라 종류에 없다"),
        ("방금 줬어", "무엇을 줬는지가 없다"),
        # 아래 둘은 골드 세트 훑기 테스트가 **실제로 잡은** 오탐이다.
        ("밥은 잘 먹고 산책도 평소처럼 잘 했어요", "기록이 아니라 되묻기에 답하는 상태 보고다"),
        ("사료 포장지의 권장량을 참고하라고 했다", "남의 말을 옮긴 것이다"),
    ],
)
def test_non_statements_are_rejected(query: str, why: str) -> None:
    assert gate.is_care_log_statement(query) is False, why


def test_walk_is_not_a_care_log_kind() -> None:
    """`care_events.kind` 에 `walk` 가 없는 것과 같은 규칙 (#332).

    산책이 여기로 들어오면 한 산책이 `walks` 와 `care_events` 두 곳에 남는다.
    """
    assert "walk" not in {kind.value for kind in CareLogKind}
    assert gate.kind_of("산책 시켰어") is None


def test_kind_lexicon_reads_one_kind_only() -> None:
    assert gate.kind_of("밥 먹였어") is CareLogKind.MEAL
    assert gate.kind_of("약 먹였어") is CareLogKind.MEDICATION
    assert gate.kind_of("간식 줬어") is CareLogKind.SNACK
    # 둘이 잡히면 안 쓴다 — 한 번의 확인으로 두 줄을 만들지 않는다.
    assert gate.kind_of("밥이랑 약 먹였어") is None


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("맘마 줬어", CareLogKind.MEAL),
        ("까까 줬어", CareLogKind.SNACK),
    ],
)
def test_baby_talk_is_in_the_lexicon(query: str, kind: CareLogKind) -> None:
    """사용자가 실제로 쓰는 말이 어휘의 원천이다 (2026-09-14 사람 요청).

    처음 어휘는 `밥`·`사료`·`간식` 같은 표준어만 있었다. `맘마`·`까까` 는 명사이고 다른
    뜻이 없어 오탐 위험이 낮다 — 골드 세트 훑기에서 늘어난 오탐이 0건이다.
    """
    assert gate.is_care_log_statement(query) is True
    assert gate.kind_of(query) is kind


@pytest.mark.parametrize("query", ["아침 줬어", "점심 줬어", "저녁 줬어", "아침 먹였어"])
def test_a_meal_time_alone_reads_as_a_meal(query: str) -> None:
    """`"아침 줬어"` — 무엇을 줬는지를 **시각**으로만 말하는 꼴 (2026-09-14 사람 요청)."""
    assert gate.is_care_log_statement(query) is True
    assert gate.kind_of(query) is CareLogKind.MEAL


@pytest.mark.parametrize(
    ("query", "kind", "why"),
    [
        ("아침에 약 먹였어", CareLogKind.MEDICATION, "명시 어휘가 시각 낱말을 이긴다"),
        ("아침 간식 줬어", CareLogKind.SNACK, "같은 이유"),
        ("아침 밥 줬어", CareLogKind.MEAL, "둘 다 밥이라 충돌이 아니다"),
    ],
)
def test_an_explicit_kind_beats_a_meal_time(
    query: str, kind: CareLogKind, why: str
) -> None:
    """**시각 낱말은 동급이 아니라 약한 신호다** (`_MEAL_TIME`).

    동급으로 넣었을 때 앞의 둘이 `None` 이 됐다 — 밥·약 두 종류가 잡혀서다. 즉 이미 되던
    투약·간식 기록이 시각 낱말 하나 때문에 **안 되는** 회귀였다.
    """
    assert gate.kind_of(query) is kind, why


@pytest.mark.parametrize(
    ("query", "why"),
    [
        ("아침에 목욕했어", "시각 낱말 + 일반 동사는 급여가 아니다"),
        ("아침 청소 다 했어", "같은 이유 — 시각은 '무엇을' 을 말하지 않는다"),
    ],
)
def test_a_meal_time_needs_a_giving_verb(query: str, why: str) -> None:
    """`했어` 까지 받으면 시각 낱말이 붙은 **모든 집안일**이 밥 기록 제안이 된다 (`_GAVE`)."""
    assert gate.is_care_log_statement(query) is False, why
    assert gate.kind_of(query) is None, why


def test_two_meal_times_are_two_meals_so_nothing_is_written() -> None:
    """`"아침이랑 저녁 다 줬어"` — 끼니가 둘이다. 한 번의 확인이 두 줄을 만들지 않는다."""
    assert gate.kind_of("아침이랑 저녁 다 줬어") is None


@pytest.mark.parametrize(
    "query",
    [
        "예약했어",
        "치약 짜뒀어",
        "약간 남겼어",
        "약속 있어",
        "약국 다녀왔어",
        # 아래 둘은 아래 골드 세트 훑기 테스트가 **실제로 잡은** 오탐이다 —
        # 처음 어휘는 못 쓸 글자를 하나씩 빼는 식이라 이 둘을 못 걸렀다.
        "주차 제약만 해제했다",
        "보험 약관 확인했어",
    ],
)
def test_medication_lexicon_does_not_fire_on_lookalikes(query: str) -> None:
    """`약` 을 품은 흔한 낱말이 투약으로 읽히면 하지 않은 투약이 기록된다."""
    assert gate.kind_of(query) is not CareLogKind.MEDICATION


@pytest.mark.parametrize("query", ["약 먹였어", "약을 먹였어", "한약 먹였어", "구충약 먹였어"])
def test_medication_lexicon_still_reads_real_doses(query: str) -> None:
    """좁히면서 진짜 약을 놓치지 않았는가 — 조사가 붙은 꼴이 특히 흔하다."""
    assert gate.kind_of(query) is CareLogKind.MEDICATION


@pytest.mark.parametrize("query", ["네", "넵", "응", "ㅇㅇ", "그래 기록해줘", "ok", "좋아"])
def test_affirmations(query: str) -> None:
    assert gate.confirmation_of(query) == "affirm"


@pytest.mark.parametrize("query", ["아니", "아니야 취소", "됐어", "아직", "나중에 할게", "no"])
def test_declines(query: str) -> None:
    assert gate.confirmation_of(query) == "decline"


@pytest.mark.parametrize("query", ["산책 갈까?", "어제 먹였어", "병원 어디가 좋아"])
def test_unrelated_is_neither(query: str) -> None:
    """승낙도 거절도 아닌 발화. 제안을 흘리고 평소대로 라우팅된다."""
    assert gate.confirmation_of(query) == "unrelated"


def test_decline_beats_affirm_when_both_appear() -> None:
    """섞이면 **안 쓰는 쪽**으로 떨어진다."""
    assert gate.confirmation_of("응 아니야 말고") == "decline"


def test_short_affirmation_must_be_the_whole_utterance() -> None:
    """`어` 한 글자는 승낙이지만, `어제 먹였어` 는 아니다."""
    assert gate.confirmation_of("어") == "affirm"
    assert gate.confirmation_of("어제 먹였어") != "affirm"


# ── 계획 ──────────────────────────────────────────────────────────────────

PET = uuid.uuid4()
_WRITABLE = {"active_dog_id": str(PET), "care_log_writable": True}


def _route(query: str, context: dict, *, write: bool = True):
    return planner.resolve_care_log_route(
        query=query, context=context, now=NOW, care_log_write=write
    )


def test_statement_becomes_a_clarify_that_shows_the_time() -> None:
    plan = _route("방금 밥 먹였어", _WRITABLE)
    assert plan.clarify is not None
    # 사용자가 검사할 수 있는 유일한 값이다 — 시각이 문장에 없으면 확인 단계가 아니다.
    assert "14:32" in plan.clarify.question
    assert plan.clarify.missing == ["care_log_confirmation"]
    assert plan.clarify.care_log is not None
    assert plan.clarify.care_log.kind is CareLogKind.MEAL
    assert plan.clarify.care_log.pet_id == PET
    assert plan.clarify.care_log.occurred_at == NOW
    # 아무것도 실행되지 않는다 (O-8: CLARIFY 는 배타).
    assert plan.requests == [] and plan.handoffs == []
    # 모델이 안 돌았다.
    assert plan.router is RouterKind.DETERMINISTIC
    assert plan.model is None and plan.prompt_version is None


@pytest.mark.parametrize(
    ("query", "context", "write", "why"),
    [
        ("방금 밥 먹였어", _WRITABLE, False, "플래그가 꺼졌다"),
        ("방금 밥 먹였어", {"active_dog_id": str(PET)}, True, "쓸 수 있는 요청이 아니다"),
        ("방금 밥 먹였어", {"care_log_writable": True}, True, "어느 아이인지 모른다"),
        ("방금 밥 먹였어", {"active_dog_id": "not-a-uuid", "care_log_writable": True}, True,
         "active_dog_id 모양이 틀렸다"),
        ("밥이랑 약 먹였어", _WRITABLE, True, "종류가 둘이다"),
    ],
)
def test_missing_pieces_hand_off_to_the_screen(
    query: str, context: dict, write: bool, why: str
) -> None:
    """**추측해서 쓰지 않는다.** 없으면 사람이 화면에서 적게 보낸다."""
    plan = _route(query, context, write=write)
    assert plan.clarify is None, why
    assert [h.target for h in plan.handoffs] == ["care_log"], why
    assert plan.handoffs[0].reason == "care_log_entry_required"
    assert plan.requests == []


def test_non_statement_leaves_routing_alone() -> None:
    """게이트가 거짓이면 `None` 이고, 그러면 오늘과 똑같이 라우팅된다."""
    assert _route("밥 언제 줘야 해?", _WRITABLE) is None


def _proposal(kind: CareLogKind = CareLogKind.MEAL, *, at: datetime = NOW) -> CareLogProposal:
    return CareLogProposal(kind=kind, pet_id=PET, occurred_at=at, proposal_id=uuid.uuid4())


def test_only_an_affirmation_opens_the_write() -> None:
    proposal = _proposal()
    plan = planner.resolve_care_log_write(query="네", pending=proposal, now=NOW)
    assert plan is not None
    assert [r.capability for r in plan.requests] == [CapabilityName.CARE_LOG]
    # **옮기기만 한다** — 확인 단계의 약속은 "보여 준 것만 들어간다".
    assert plan.requests[0].payload == proposal
    assert plan.router is RouterKind.DETERMINISTIC


@pytest.mark.parametrize("query", ["아니", "산책 갈까?", ""])
def test_anything_but_an_affirmation_writes_nothing(query: str) -> None:
    assert planner.resolve_care_log_write(query=query, pending=_proposal(), now=NOW) is None


def test_no_pending_proposal_writes_nothing() -> None:
    assert planner.resolve_care_log_write(query="네", pending=None, now=NOW) is None


def test_a_stale_proposal_writes_nothing() -> None:
    """대기는 한 턴짜리지만 그 한 턴이 며칠 전일 수 있다 (`PROPOSAL_TTL`)."""
    late = NOW + gate.PROPOSAL_TTL + timedelta(minutes=1)
    assert planner.resolve_care_log_write(query="네", pending=_proposal(), now=late) is None
    # 경계 안쪽은 쓴다.
    on_time = NOW + gate.PROPOSAL_TTL
    assert planner.resolve_care_log_write(query="네", pending=_proposal(), now=on_time) is not None


def test_care_log_is_not_a_routing_signal_at_all() -> None:
    """`care_log` 는 쓰기도 HANDOFF 도 **명시 신호로 못 부른다.**

    `_EXECUTE_NAMES` 에 없어서 쓰기가 안 열리고, `_HANDOFF_REASONS` 에도 없어서 HANDOFF 도
    안 열린다 — 해소되지 않은 신호이므로 `None` 이고, 의미 라우팅이 평소대로 답을 정한다.

    후자를 일부러 이렇게 둔 이유가 있다: `_HANDOFF_REASONS` 의 키는 곧
    `semantic.HandoffName` 의 값이라, 라우터 스키마에 없는 이름을 넣으면 그 신호가 500 이
    된다. 처음에 그렇게 넣었고 이 테스트가 잡았다.
    """
    assert (
        planner.resolve_deterministic_route(
            requested_capability="care_log", query="아무 말", context={}
        )
        is None
    )
    assert "care_log" not in planner._HANDOFF_REASONS
    assert "care_log" not in planner._EXECUTE_NAMES


# ── 턴을 건너 제안이 살아남는가 ─────────────────────────────────────────────


def test_proposal_survives_the_json_round_trip_of_a_stored_turn() -> None:
    """확인 단계가 **상태 없이** 되는 근거. 새 칸도 새 테이블도 없다.

    `services/chat.public_response_of` 가 `model_dump(mode="json")` 이므로, 이 왕복이
    되면 `pending_clarification_of` 가 다음 턴에 제안을 그대로 읽는다.
    """
    original = _proposal(CareLogKind.MEDICATION)
    stored = ClarifyRequest(
        question="14:32에 약 먹인 걸로 기록할까요?",
        missing=["care_log_confirmation"],
        care_log=original,
    ).model_dump(mode="json")

    revived = ClarifyRequest.model_validate(stored)
    assert revived.care_log == original


def test_pending_clarification_reads_the_proposal_off_the_last_turn() -> None:
    from daengs_backend.services.chat import pending_clarification_of

    proposal = _proposal()
    turn = _FakeTurn(
        public_response={
            "clarify": {
                "question": "14:32에 밥 먹인 걸로 기록할까요?",
                "missing": ["care_log_confirmation"],
                "missing_axes": [],
                "care_log": proposal.model_dump(mode="json"),
            }
        }
    )
    pending = pending_clarification_of([turn])
    assert pending is not None
    assert pending.care_log == proposal


@pytest.mark.parametrize(
    "clarify",
    [
        {"question": "위도를 알려주세요", "missing": ["location.lat"]},
        {"question": "물어봅니다", "missing": ["observation"], "care_log": None},
        {"question": "깨진 값", "missing": ["care_log_confirmation"], "care_log": {"kind": "밥"}},
        {"question": "모양이 다름", "missing": ["care_log_confirmation"], "care_log": "meal"},
    ],
)
def test_other_or_broken_clarifications_carry_no_proposal(clarify: dict) -> None:
    """**읽다 실패하면 안 쓰는 쪽으로 떨어진다.** 옛 행에는 이 칸이 아예 없다."""
    from daengs_backend.services.chat import pending_clarification_of

    pending = pending_clarification_of([_FakeTurn(public_response={"clarify": clarify})])
    assert pending is not None
    assert pending.care_log is None


@dataclass
class _FakeTurn:
    """`pending_clarification_of` 가 읽는 칸만 가진 대역."""

    public_response: dict
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    assistant_status: str = "CLARIFY"


# ── 어댑터 (실제로 쓰는 자리) ───────────────────────────────────────────────


@dataclass
class FakeCareEvent:
    actor_app_user_id: uuid.UUID
    pet_id: uuid.UUID
    kind: str
    occurred_at: datetime
    note: str | None
    client_event_id: uuid.UUID
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime(2026, 9, 13, tzinfo=UTC))


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    s.pets.append(FakePet(app_user_id=OWNER, id=PET, name="댕댕", breed="dog_pug"))
    return s


@pytest.fixture
def events(store: Store, monkeypatch: pytest.MonkeyPatch) -> list[FakeCareEvent]:
    rows: list[FakeCareEvent] = []

    def add(session, event):
        fake = FakeCareEvent(
            actor_app_user_id=event.actor_app_user_id,
            pet_id=event.pet_id,
            kind=event.kind,
            occurred_at=event.occurred_at,
            note=event.note,
            client_event_id=event.client_event_id,
        )
        event.id = fake.id
        event.created_at = fake.created_at
        rows.append(fake)
        return event

    async def get_by_client_event(session, pet_id, client_event_id):
        return next(
            (e for e in rows if e.pet_id == pet_id and e.client_event_id == client_event_id),
            None,
        )

    async def list_kind_between(session, pet_ids, kind, start, end):
        # **`pet_ids` 는 묶음이다** (MVP 결정 §7) — 같은 실제 강아지를 두 사람이 각자
        # 등록해 연결하면 약 중복 창이 그룹 전체를 봐야 한다. 여기 대역이 단일 id 를
        # 비교하고 있었더니 창이 아무것도 못 찾아 **중복 투약이 그냥 기록됐다** — 이 파일의
        # 중복 테스트가 그것을 잡았다. `test_care_events.py` 의 대역과 같은 모양으로 둔다.
        wanted = set(pet_ids)
        return sorted(
            (
                e
                for e in rows
                if e.pet_id in wanted and e.kind == kind and start <= e.occurred_at <= end
            ),
            key=lambda e: e.occurred_at,
            reverse=True,
        )

    async def count_walks(session, app_user_id, pet_ids, start, end):
        return 0

    monkeypatch.setattr(care_repo, "add", add)
    monkeypatch.setattr(care_repo, "get_by_client_event", get_by_client_event)
    monkeypatch.setattr(care_repo, "list_kind_between", list_kind_between)
    monkeypatch.setattr(walk_repo, "count_for_pet_between", count_walks)
    return rows


class _SessionFactory:
    """`async with factory() as session` 만 흉내 냅니다."""

    def __init__(self, session: object) -> None:
        self._session = session

    def __call__(self) -> "_SessionFactory":
        return self

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *_: object) -> bool:
        return False


def _adapter(*, app_user_id: uuid.UUID = OWNER, session: object | None = None):
    return CareLogCapabilityAdapter(
        session_factory=_SessionFactory(session or FakeSession()),  # type: ignore[arg-type]
        app_user_id=app_user_id,
    )


def _request(proposal: CareLogProposal) -> CapabilityRequest:
    return CapabilityRequest.model_validate(
        {"capability": "care_log", "payload": proposal, "timeout_ms": None}
    )


@pytest.mark.anyio
async def test_the_adapter_writes_exactly_the_confirmed_proposal(
    events: list[FakeCareEvent],
) -> None:
    proposal = _proposal()
    result = await _adapter().run(_request(proposal), request_id="r1")

    assert result.status is CapabilityStatus.OK
    assert result.data is not None
    assert result.data["answer"] == "14:32에 밥 기록했어요."
    assert result.data["created"] is True
    assert len(events) == 1
    row = events[0]
    assert row.kind == "meal"
    assert row.occurred_at == proposal.occurred_at
    assert row.pet_id == PET
    # 멱등키는 제안 id 다 — 같은 제안에 두 번 승낙해도 한 줄인 근거.
    assert row.client_event_id == proposal.proposal_id
    # 메모는 안 만든다 — 사용자가 적지 않은 자유 텍스트를 서버가 지어내지 않는다.
    assert row.note is None
    # 챙긴 사람은 말한 사람이다.
    assert row.actor_app_user_id == OWNER


@pytest.mark.anyio
async def test_confirming_the_same_proposal_twice_writes_one_row(
    events: list[FakeCareEvent],
) -> None:
    """첫 응답을 못 본 재시도와 두 번 말한 것을 우리가 못 가른다 — 그래서 한 줄이다."""
    proposal = _proposal()
    first = await _adapter().run(_request(proposal), request_id="r1")
    second = await _adapter().run(_request(proposal), request_id="r2")

    assert len(events) == 1
    assert first.data["created"] is True  # type: ignore[index]
    assert second.data["created"] is False  # type: ignore[index]
    # 사용자에게는 같은 문장이 나간다.
    assert first.data["answer"] == second.data["answer"]  # type: ignore[index]


@pytest.mark.anyio
async def test_medication_conflict_abstains_instead_of_forcing_the_write(
    events: list[FakeCareEvent],
) -> None:
    """채팅이 약 중복 창을 못 건너뛴다 (`docs/co-care.md` §4).

    사용자가 승낙한 것은 "지금 약 기록" 이고, 중복 경고는 **다른 질문**이다.
    """
    await _adapter().run(_request(_proposal(CareLogKind.MEDICATION)), request_id="r1")
    assert len(events) == 1

    # 2시간 뒤 같은 약 — `MEDICATION_CONFIRM_WINDOW`(6시간) 안이다.
    later = _proposal(CareLogKind.MEDICATION, at=NOW + timedelta(hours=2))
    result = await _adapter().run(_request(later), request_id="r2")

    assert result.status is CapabilityStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.code == "care_log.medication_conflict"
    assert "기록 화면" in result.abstention.message
    assert len(events) == 1, "두 번째는 안 써야 한다"


@pytest.mark.anyio
async def test_a_pet_the_speaker_does_not_care_for_abstains(
    events: list[FakeCareEvent],
) -> None:
    stranger = uuid.uuid4()
    result = await _adapter(app_user_id=stranger).run(_request(_proposal()), request_id="r1")

    assert result.status is CapabilityStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.code == "care_log.pet_not_accessible"
    assert events == []


@pytest.mark.anyio
async def test_a_db_failure_is_an_error_that_never_claims_success(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`care_events` 표가 아직 없는 서버도 여기로 온다."""

    async def boom(*_: object, **__: object):
        raise OperationalError("select 1", {}, Exception("relation care_events does not exist"))

    monkeypatch.setattr(care_repo, "get_by_client_event", boom)
    result = await _adapter().run(_request(_proposal()), request_id="r1")

    assert result.status is CapabilityStatus.ERROR
    assert result.error is not None
    assert result.error.kind == "care_log_write_failed"
    # 원문은 로그로만 간다 (D-037).
    assert "relation" not in result.error.detail


# ── 두 턴이 이어지는 전체 흐름 ───────────────────────────────────────────────


class _NeverCalledRouter:
    """모델이 불리면 테스트가 실패한다 — 쓰기 경로의 모델 호출은 0회다."""

    async def select(self, **_: object):  # pragma: no cover - 불리면 아래 assert 가 잡는다
        raise AssertionError("의미 라우터가 케어 기록 경로에서 불렸다")


class _NeverCalledResolver:
    async def resolve(self, **_: object):  # pragma: no cover - 같은 이유
        raise AssertionError("Turn Resolver 가 케어 기록 경로에서 불렸다")


def _service(**engine_kwargs) -> AssistantOrchestrationService:
    from daengs_backend.orchestration.graph import OrchestrationEngine

    return AssistantOrchestrationService(
        engine=OrchestrationEngine(**engine_kwargs),
        semantic_router=_NeverCalledRouter(),  # type: ignore[arg-type]
        turn_resolver=_NeverCalledResolver(),  # type: ignore[arg-type]
    )


def _principal():
    from daengs_backend.orchestration.contracts import PrincipalContext

    return PrincipalContext(subject=str(OWNER), kind="APP_USER")


@pytest.mark.anyio
async def test_two_turns_end_in_one_row_without_calling_any_model(
    events: list[FakeCareEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    from daengs_backend.config import settings
    from daengs_backend.orchestration.resolver import PendingClarification
    from daengs_backend.services.chat import public_response_of

    monkeypatch.setattr(settings, "care_log_write", True)
    service = _service(care_log_adapter=_adapter())

    first = await service.run(
        query="방금 밥 먹였어",
        principal=_principal(),
        context=dict(_WRITABLE),
    )
    assert first.status is AssistantStatus.CLARIFY
    assert first.clarify is not None and first.clarify.care_log is not None
    assert "14:3" in first.clarify.question or ":" in first.clarify.question
    assert events == [], "첫 턴은 아무것도 안 쓴다"

    # 앱이 저장하는 것과 같은 모양으로 왕복시킨다.
    stored = public_response_of(first)
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question=stored["clarify"]["question"],  # type: ignore[index]
        missing=stored["clarify"]["missing"],  # type: ignore[index]
        care_log=CareLogProposal.model_validate(stored["clarify"]["care_log"]),  # type: ignore[index]
    )

    second = await service.run(
        query="네",
        principal=_principal(),
        context=dict(_WRITABLE),
        pending_clarification=pending,
    )
    assert second.status is AssistantStatus.ANSWERED
    assert "기록했어요" in second.message
    assert len(events) == 1
    assert events[0].kind == "meal"


@pytest.mark.anyio
async def test_declining_writes_nothing_and_says_so(
    events: list[FakeCareEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    from daengs_backend.config import settings
    from daengs_backend.orchestration.resolver import PendingClarification

    monkeypatch.setattr(settings, "care_log_write", True)
    service = _service(care_log_adapter=_adapter())
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="14:32에 밥 먹인 걸로 기록할까요?",
        missing=["care_log_confirmation"],
        care_log=_proposal(),
    )

    response = await service.run(
        query="아니 됐어",
        principal=_principal(),
        context=dict(_WRITABLE),
        pending_clarification=pending,
    )
    assert response.status is AssistantStatus.ANSWERED
    assert "기록하지 않았어요" in response.message
    assert response.results == []
    assert events == []


@pytest.mark.anyio
async def test_with_the_flag_off_the_statement_only_hands_off(
    events: list[FakeCareEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    """플래그가 꺼진 운영에서 `"방금 밥 먹였어"` 가 받는 답 — 기록 화면 안내뿐이다."""
    from daengs_backend.config import settings

    monkeypatch.setattr(settings, "care_log_write", False)
    response = await _service().run(
        query="방금 밥 먹였어",
        principal=_principal(),
        context=dict(_WRITABLE),
    )
    assert response.status is AssistantStatus.HANDOFF
    assert [h.target for h in response.handoffs] == ["care_log"]
    assert "기록 화면" in response.message
    assert events == []


# ── 사본 대조 ──────────────────────────────────────────────────────────────


def test_care_log_kind_matches_the_http_schema() -> None:
    """`CareLogKind` 는 `schemas/care_event.CareEventKind` 의 사본이다 (그 독스트링).

    두 벌인 것은 방향 때문이고, 어긋나면 채팅이 쓸 수 없는 종류를 제안하게 된다.
    """
    assert {kind.value for kind in CareLogKind} == set(CareEventKind.__args__)


def test_proposal_timezone_is_required() -> None:
    """naive 로 받으면 어느 하루에 넣을지 서버가 추측하게 된다."""
    with pytest.raises(ValueError):
        CareLogProposal(
            kind=CareLogKind.MEAL,
            pet_id=PET,
            occurred_at=datetime(2026, 9, 13, 14, 32),  # noqa: DTZ001 — naive 가 이 테스트의 요점
            proposal_id=uuid.uuid4(),
        )


def test_clarify_question_timezone_matches_the_day_boundary() -> None:
    """확인 문장의 시각과 하루를 자르는 시간대가 같아야 한다 (`planner._CARE_LOG_TZ`).

    다르면 자정 전후에 `"23:50에 기록할까요?"` 라고 묻고 어제 칸에 넣는다.
    """
    assert str(planner._CARE_LOG_TZ) == care_service.DAY_TIMEZONE


# ── 게이트가 기존 답을 가로채지 않는가 (회귀 기준선) ──────────────────────────


def _eval_strings() -> list[tuple[str, str]]:
    """`backend/evals/**/*.jsonl` 안의 **모든 문자열**. (파일, 문자열) 쌍.

    사용자 발화만 고르지 않고 통째로 훑는 것이 의도다. 비서가 낸 답변 본문도 같이 들어오고,
    그쪽이 훨씬 적대적이다 — `"오늘 밥은 두 번 먹였고…"` 같은 문장이 답변에는 실제로 있다.
    게이트를 지나면 안 되는 텍스트의 상한을 넓게 잡는 쪽이 회귀 기준선으로 쓸모 있다.
    """
    import json

    def walk(obj: object):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for value in obj.values():
                yield from walk(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from walk(value)

    root = Path(__file__).resolve().parents[1] / "evals"
    found: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            for text in walk(row):
                if text.strip():
                    found.append((path.name, text))
    return found


#: 골드 세트에서 게이트가 **맞게** 잡는 발화. 이것 말고는 하나도 걸리면 안 된다.
#:
#: `"오늘 밥 줬어"` 는 실제로 기록 진술이다 — 골드 전체에서 유일한 참 양성이고, 거기 있는
#: 이유는 응급 어휘 게이트의 **음성 대조군**(`expect: false`)이기 때문이다. 그 eval 은
#: `is_emergency` 만 부르므로 이쪽 게이트가 그 측정을 바꾸지 않는다.
_EXPECTED_GOLD_HITS = {"오늘 밥 줬어"}


def test_the_gate_hijacks_nothing_in_the_frozen_eval_sets() -> None:
    """**골드 세트에서 게이트가 가로채는 것은 참 양성 하나뿐이다** (D-075 「오탐은 쟀습니다」).

    이것이 "이 카드는 어떤 벤치마크 행도 바꾸지 않는다" 의 근거다. `answer_quality` 수집기는
    실제로 `AssistantOrchestrationService` 를 지나므로 이 게이트가 그 경로에 있고, 한 건이라도
    잘못 걸리면 그 문항들의 라우팅이 조용히 달라진다.

    2026-09-14 기준 **460,660건 / 1건**(위 참 양성). **이 숫자가 재현율을 말하지는 않는다** —
    골드는 거의 전부 질문이라 기록 진술이 애초에 없다. 여기서 재는 것은 오탐뿐이다.

    **이 테스트는 실제로 결함 셋을 잡았다** (그래서 남겨 둔다):
    ① `주차 제약만…`·`보험 약관 확인했어` → `약` 어휘를 "못 쓸 글자를 빼는" 방식에서
       "앞뒤에 한글이 붙으면 약이 아니다" 로 뒤집었다.
    ② `"밥은 잘 먹고 산책도 평소처럼 잘 했어요"` → 되묻기에 **답하는** 발화였다. 상태 보고
       표지(`잘 먹`·`평소`·`산책` …)를 막는 줄이 여기서 나왔다.
    ③ 평가 판정문(`"…참고하라고 했다"`) → 남의 말 옮기기 표지를 막았다.

    새로 걸린 발화가 생겼다면 둘 중 하나다: 골드에 기록 진술이 새로 들어왔거나(그러면
    `_EXPECTED_GOLD_HITS` 에 넣는다), 어휘가 느슨해졌다(그러면 어휘를 좁힌다).
    """
    texts = _eval_strings()
    # 세트가 통째로 안 읽히면 0건이 "통과" 로 보인다 — 먼저 읽혔는지를 못박는다.
    assert len(texts) > 50_000, f"골드 세트를 못 읽었다: {len(texts)}건"
    caught = {
        (name, text)
        for name, text in texts
        if gate.is_care_log_statement(text) and text not in _EXPECTED_GOLD_HITS
    }
    assert caught == set(), f"골드 텍스트가 케어 기록 게이트로 빠진다: {sorted(caught)[:5]}"
    # 참 양성이 사라졌다면 어휘가 과하게 좁아진 것이다 — 그쪽도 회귀다.
    found = {text for _, text in texts if gate.is_care_log_statement(text)}
    assert found == _EXPECTED_GOLD_HITS, f"참 양성이 바뀌었다: {found}"
