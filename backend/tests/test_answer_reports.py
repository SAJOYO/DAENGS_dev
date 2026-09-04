"""AI 답변 신고 (A1 · D-053).

D-053 이 정한 넷을 코드가 실제로 지키는지 봅니다. **문서에만 있고 코드가 안 지키면
결정이 없는 것과 같아서**, 이 파일이 그 결정의 회귀 테스트입니다.

  ① 열람 범위 — 상세에 나가는 원문은 **신고된 turn 하나**뿐이고, 나머지는 숫자다
  ② 순번 — "n턴 중 m번째" 가 실제로 맞다. 이 숫자가 나중에 범위를 넓힐 근거가 된다
  ③ 감사 — **상세만** 남고 목록은 안 남는다. 남는 행에 원문이 들어가지 않는다
  ④ 소유 — 남의 turn 은 신고할 수 없고, 없는 것과 구분해 주지도 않는다

`FakeSession` 이 커밋 경계를 흉내 내므로(tests/fakes.py) ③은 `store.audit_log`(확정된
것)와 `store.audit_pending`(세션에 얹기만 한 것)의 차이로 봅니다.
"""

import uuid
from datetime import UTC, datetime

import pytest
from fakes import (
    IP,
    FakeAdmin,
    FakeAppUser,
    FakeChatSession,
    FakeChatTurn,
    FakeSession,
    Store,
    install,
)

from daengs_backend.models import AUDIT_REPORT_RESOLVED, AUDIT_REPORT_TURN_REVEALED
from daengs_backend.services import answer_report as service


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    return install(Store(FakeAdmin()), monkeypatch)


@pytest.fixture
def member(store: Store) -> FakeAppUser:
    return store.add_app_user(FakeAppUser(kakao_id=300001))


@pytest.fixture
def other(store: Store) -> FakeAppUser:
    return store.add_app_user(FakeAppUser(kakao_id=300002))


@pytest.fixture
def session(store: Store) -> FakeSession:
    return FakeSession(store)


def _chat(store: Store, owner: FakeAppUser, turns: int = 3) -> FakeChatSession:
    """완료 turn 이 `turns` 개인 대화 하나를 만듭니다."""
    chat = FakeChatSession(
        app_user_id=owner.id, pet_id=uuid.uuid4(), title="산책 이야기"
    )
    store.chat_sessions.append(chat)
    for i in range(turns):
        store.chat_turns.append(
            FakeChatTurn(
                session_id=chat.id,
                client_message_id=uuid.uuid4(),
                processing_status="completed",
                user_content=f"질문 {i}",
                assistant_content=f"답변 {i}",
                assistant_status="ANSWERED",
                request_id=f"req-{i}",
                public_response={"status": "ANSWERED", "citations": []},
                created_at=datetime(2026, 9, 1, 12, i, tzinfo=UTC),
            )
        )
    return chat


def _turns_of(store: Store, chat: FakeChatSession) -> list[FakeChatTurn]:
    return [t for t in store.chat_turns if t.session_id == chat.id]


class TestCreate:
    async def test_본인_대화면_신고된다(self, session, store, member) -> None:
        chat = _chat(store, member)
        target = _turns_of(store, chat)[1]

        report = await service.create(
            session,  # type: ignore[arg-type]
            turn_id=target.id,
            app_user_id=member.id,
            reason="사실과 다릅니다",
        )

        assert report.turn_id == target.id
        assert report.status == "open"
        assert report.reviewed_by is None and report.reviewed_at is None

    async def test_남의_대화는_신고할_수_없다(
        self, session, store, member, other
    ) -> None:
        """**이 조인을 빠뜨리면 남의 대화가 관리자 화면에 뜨는 길이 열립니다.**"""
        chat = _chat(store, other)
        target = _turns_of(store, chat)[0]

        with pytest.raises(service.TurnNotFoundError):
            await service.create(
                session,  # type: ignore[arg-type]
                turn_id=target.id,
                app_user_id=member.id,
                reason="아무거나",
            )

    async def test_없는_turn_과_남의_turn_이_같은_예외다(
        self, session, store, member, other
    ) -> None:
        """구분해 주면 남의 turn id 가 실재하는지 확인하는 길이 됩니다."""
        chat = _chat(store, other)
        theirs = _turns_of(store, chat)[0]

        with pytest.raises(service.TurnNotFoundError):
            await service.create(
                session,  # type: ignore[arg-type]
                turn_id=theirs.id,
                app_user_id=member.id,
                reason="x",
            )
        with pytest.raises(service.TurnNotFoundError):
            await service.create(
                session,  # type: ignore[arg-type]
                turn_id=uuid.uuid4(),
                app_user_id=member.id,
                reason="x",
            )

    async def test_같은_사람이_두_번_신고하면_막힌다(
        self, session, store, member
    ) -> None:
        chat = _chat(store, member)
        target = _turns_of(store, chat)[0]
        await service.create(
            session, turn_id=target.id, app_user_id=member.id, reason="1"  # type: ignore[arg-type]
        )

        with pytest.raises(service.AlreadyReportedError):
            await service.create(
                session, turn_id=target.id, app_user_id=member.id, reason="2"  # type: ignore[arg-type]
            )

    async def test_다른_사람은_같은_답변을_신고할_수_있다(
        self, session, store, member, other
    ) -> None:
        """몇 번 신고됐는지가 그 답변이 얼마나 나쁜지의 신호입니다 — 막지 않습니다."""
        chat = _chat(store, member)
        target = _turns_of(store, chat)[0]
        other_chat = _chat(store, other)
        # 같은 turn 을 다른 사람이 신고하려면 그 사람 소유여야 하므로, 여기서는
        # 소유 검사를 지나도록 대화의 주인을 바꿔 끼웁니다.
        chat.app_user_id = other.id
        del other_chat

        await service.create(
            session, turn_id=target.id, app_user_id=other.id, reason="나도"  # type: ignore[arg-type]
        )
        assert len(store.answer_reports) == 1


class TestDetail:
    async def test_원문은_신고된_turn_하나뿐이다(
        self, session, store, member
    ) -> None:
        """**D-053 ① 의 회귀 테스트입니다.** 상세가 대화 전체를 실어 나르면 안 됩니다."""
        chat = _chat(store, member, turns=5)
        target = _turns_of(store, chat)[2]
        report = await service.create(
            session, turn_id=target.id, app_user_id=member.id, reason="틀림"  # type: ignore[arg-type]
        )

        detail = await service.get_detail(
            session, report_id=report.id, actor_id=store.admin.id  # type: ignore[arg-type]
        )

        assert detail is not None
        assert detail.turn.id == target.id
        assert detail.turn.user_content == "질문 2"
        # 돌려주는 turn 은 하나다 — 목록형 필드가 없다.
        assert not hasattr(detail, "turns")

    async def test_순번이_맞다(self, session, store, member) -> None:
        """"5턴 중 3번째" — **숫자만** 나갑니다 (D-053 ①)."""
        chat = _chat(store, member, turns=5)
        target = _turns_of(store, chat)[2]
        report = await service.create(
            session, turn_id=target.id, app_user_id=member.id, reason="틀림"  # type: ignore[arg-type]
        )

        detail = await service.get_detail(
            session, report_id=report.id, actor_id=store.admin.id  # type: ignore[arg-type]
        )

        assert detail is not None
        assert (detail.session_turn_count, detail.position) == (5, 3)

    async def test_상세를_열면_감사_행이_확정된다(
        self, session, store, member
    ) -> None:
        """`audit_pending` 이 아니라 `audit_log` 여야 합니다 — 커밋까지 됐다는 뜻입니다."""
        chat = _chat(store, member)
        target = _turns_of(store, chat)[0]
        report = await service.create(
            session, turn_id=target.id, app_user_id=member.id, reason="틀림"  # type: ignore[arg-type]
        )

        await service.get_detail(
            session, report_id=report.id, actor_id=store.admin.id, ip=IP  # type: ignore[arg-type]
        )

        (entry,) = store.audit_log
        assert entry.action == AUDIT_REPORT_TURN_REVEALED
        assert entry.admin_user_id == store.admin.id
        assert entry.target_type == "app_user"
        assert entry.target_id == member.id

    async def test_감사_행에_원문이_안_들어간다(
        self, session, store, member
    ) -> None:
        """넣으면 `admin_audit_log` 가 두 번째 대화 저장소가 됩니다."""
        chat = _chat(store, member)
        target = _turns_of(store, chat)[0]
        report = await service.create(
            session, turn_id=target.id, app_user_id=member.id, reason="틀림"  # type: ignore[arg-type]
        )

        await service.get_detail(
            session, report_id=report.id, actor_id=store.admin.id  # type: ignore[arg-type]
        )

        (entry,) = store.audit_log
        assert entry.detail == {"report_id": str(report.id)}
        blob = str(entry.detail)
        assert "질문" not in blob and "답변" not in blob

    async def test_없는_신고는_None(self, session, store) -> None:
        assert (
            await service.get_detail(
                session, report_id=uuid.uuid4(), actor_id=store.admin.id  # type: ignore[arg-type]
            )
            is None
        )


class TestList:
    async def test_목록은_감사에_안_남는다(self, session, store, member) -> None:
        """**D-053 ③ 의 회귀 테스트입니다.** 목록까지 남기면 진짜 따져야 하는 행위가
        그 안에 묻힙니다 (`pii_revealed` 가 그은 선)."""
        chat = _chat(store, member)
        target = _turns_of(store, chat)[0]
        await service.create(
            session, turn_id=target.id, app_user_id=member.id, reason="틀림"  # type: ignore[arg-type]
        )

        await service.list_reports(session)  # type: ignore[arg-type]

        assert store.audit_log == []
        assert store.audit_pending == []

    async def test_신고_수가_함께_나온다(self, session, store, member, other) -> None:
        chat = _chat(store, member)
        target = _turns_of(store, chat)[0]
        await service.create(
            session, turn_id=target.id, app_user_id=member.id, reason="1"  # type: ignore[arg-type]
        )
        chat.app_user_id = other.id
        await service.create(
            session, turn_id=target.id, app_user_id=other.id, reason="2"  # type: ignore[arg-type]
        )

        page = await service.list_reports(session)  # type: ignore[arg-type]

        assert len(page.reports) == 2
        assert {v.report_count for v in page.reports} == {2}

    async def test_상태로_거른다(self, session, store, member) -> None:
        chat = _chat(store, member, turns=2)
        first, second = _turns_of(store, chat)
        a = await service.create(
            session, turn_id=first.id, app_user_id=member.id, reason="1"  # type: ignore[arg-type]
        )
        await service.create(
            session, turn_id=second.id, app_user_id=member.id, reason="2"  # type: ignore[arg-type]
        )
        await service.set_status(
            session, report_id=a.id, status="dismissed", actor_id=store.admin.id  # type: ignore[arg-type]
        )

        opened = await service.list_reports(session, status="open")  # type: ignore[arg-type]
        assert [v.report.id for v in opened.reports] == [
            r.id for r in store.answer_reports if r.status == "open"
        ]

    async def test_커서가_다음_쪽을_준다(self, session, store, member) -> None:
        chat = _chat(store, member, turns=3)
        for turn in _turns_of(store, chat):
            await service.create(
                session, turn_id=turn.id, app_user_id=member.id, reason="x"  # type: ignore[arg-type]
            )

        first = await service.list_reports(session, limit=2)  # type: ignore[arg-type]
        assert len(first.reports) == 2
        assert first.next_cursor is not None

        second = await service.list_reports(  # type: ignore[arg-type]
            session, limit=2, cursor=first.next_cursor
        )
        assert len(second.reports) == 1
        assert second.next_cursor is None

    async def test_손으로_고친_커서는_거절한다(self, session) -> None:
        with pytest.raises(service.InvalidCursorError):
            await service.list_reports(session, cursor="not-a-cursor")  # type: ignore[arg-type]


class TestSetStatus:
    async def test_처리하면_누가_언제가_같이_찍힌다(
        self, session, store, member
    ) -> None:
        """SQL 의 `answer_reports_review_state_check` 가 한쪽만 채워진 행을 막습니다."""
        chat = _chat(store, member)
        target = _turns_of(store, chat)[0]
        report = await service.create(
            session, turn_id=target.id, app_user_id=member.id, reason="틀림"  # type: ignore[arg-type]
        )

        updated = await service.set_status(
            session, report_id=report.id, status="reviewed", actor_id=store.admin.id  # type: ignore[arg-type]
        )

        assert updated is not None
        assert updated.status == "reviewed"
        assert updated.reviewed_by == store.admin.id
        assert updated.reviewed_at is not None

    async def test_처리도_감사에_남는다(self, session, store, member) -> None:
        chat = _chat(store, member)
        target = _turns_of(store, chat)[0]
        report = await service.create(
            session, turn_id=target.id, app_user_id=member.id, reason="틀림"  # type: ignore[arg-type]
        )

        await service.set_status(
            session, report_id=report.id, status="dismissed", actor_id=store.admin.id  # type: ignore[arg-type]
        )

        (entry,) = store.audit_log
        assert entry.action == AUDIT_REPORT_RESOLVED
        assert entry.detail == {"report_id": str(report.id), "status": "dismissed"}

    async def test_없는_신고는_None(self, session, store) -> None:
        assert (
            await service.set_status(
                session,  # type: ignore[arg-type]
                report_id=uuid.uuid4(),
                status="reviewed",
                actor_id=store.admin.id,
            )
            is None
        )
