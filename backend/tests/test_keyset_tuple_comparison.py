"""키셋 페이지네이션의 `(created_at, id)` 비교가 SQL 튜플 비교로 내려가는지.

**배경 (2026-09-18 발견).** `repositories/admin_audit_log.py` · `answer_report.py` ·
`gait_record.py` 넷이 파이썬 튜플끼리 `<`/`>` 를 썼습니다
(`(Model.created_at, Model.id) < (at, last_id)`). SQLAlchemy 의 컬럼 비교 연산자는
이걸 "SQL 튜플 비교"로 읽지 않고, 파이썬이 튜플을 원소별로 비교하다 **첫 원소에서
멈춥니다** — `bool(Model.created_at < at)` 가 참(컬럼 비교식은 늘 참인 객체)이라 둘째
원소(`id`)는 아예 평가되지 않습니다. 그 결과 컴파일된 SQL 에는 `created_at < :at` 만
남고 `id` 비교가 빠집니다.

**증상**: 같은 `created_at` 을 가진 행이 여러 개면(동시 가입/로그인/신고 등), 첫 페이지의
마지막 행과 같은 시각인 나머지 행들이 다음 페이지에서 조용히 건너뛰어집니다 — 에러도
경고도 없이, 그 시각에 있던 행 일부가 영영 안 보입니다.

**고침**: `sqlalchemy.tuple_()` 로 감싸 `tuple_(a, b) < tuple_(x, y)` 로 쓰면 SQL
`(a, b) < (x, y)` 로 그대로 내려갑니다 (`app_user.py:168` · `admin_ai_card.py:48` 이
이미 이렇게 하고 있었습니다).

이 파일은 그 회귀를 **DB 없이** 잡습니다 — 컴파일된 SQL 문자열에 `id` 비교가 실제로
있는지만 봅니다. 고치기 전 코드로 돌리면 전부 실패합니다(직접 되돌려서 확인함,
2026-09-18).
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql

from daengs_backend.repositories import admin_audit_log, answer_report, gait_record

AT = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
LAST_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _compiled(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def _empty_session() -> SimpleNamespace:
    """`execute()` 만 필요한 자리에 쓰는 최소 대역. 실제 DB 를 안 탑니다."""
    return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=list)))


async def test_admin_audit_log_list_entries_compares_the_full_tuple_in_sql() -> None:
    session = _empty_session()

    await admin_audit_log.list_entries(session, limit=10, before=(AT, LAST_ID))

    stmt = session.execute.call_args.args[0]
    sql = _compiled(stmt)
    assert "(admin_audit_log.created_at, admin_audit_log.id) <" in sql
    assert str(LAST_ID) in sql, "id 가 비교식에서 빠지면(파이썬 튜플 비교로 풀리면) 여기 없습니다"


async def test_answer_report_list_reports_compares_the_full_tuple_in_sql() -> None:
    session = _empty_session()

    await answer_report.list_reports(session, limit=10, before=(AT, LAST_ID))

    stmt = session.execute.call_args.args[0]
    sql = _compiled(stmt)
    assert "(answer_reports.created_at, answer_reports.id) <" in sql
    assert str(LAST_ID) in sql


async def test_answer_report_turn_position_compares_the_full_tuple_in_sql() -> None:
    """`before` 계산(순번 매기기)도 같은 함정이 있었습니다 — `turn_position`."""
    calls: list = []

    async def scalar(stmt):
        calls.append(stmt)
        return 0

    session = SimpleNamespace(scalar=scalar)
    turn = SimpleNamespace(
        session_id=uuid.uuid4(),
        created_at=AT,
        id=LAST_ID,
        processing_status="completed",
    )

    await answer_report.turn_position(session, turn)

    # 두 번째 session.scalar 호출이 "몇 건이 이 turn 보다 앞이었나"를 세는 쪽입니다.
    sql = _compiled(calls[1])
    assert "(chat_turns.created_at, chat_turns.id) <" in sql
    assert str(LAST_ID) in sql


async def test_gait_record_list_for_pet_compares_the_full_tuple_in_sql(
    monkeypatch,
) -> None:
    """커서가 있을 때(다음 쪽 요청) 붙는 조건 — anchor 와 같은 시각인 기록을 건너뛰면
    안 됩니다."""
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=list))
    )
    app_user_id = uuid.uuid4()
    pet_id = uuid.uuid4()
    cursor = uuid.uuid4()
    anchor = SimpleNamespace(created_at=AT, id=LAST_ID)

    # get_accessible 은 별도의 session.execute 호출(권한 조회)이라 여기서는 그 결과만
    # 필요합니다 — 세는 대상은 그다음에 붙는 목록 쿼리입니다.
    monkeypatch.setattr(gait_record, "get_accessible", AsyncMock(return_value=anchor))

    await gait_record.list_for_pet(
        session, app_user_id, pet_id, limit=10, cursor=cursor
    )

    stmt = session.execute.call_args.args[0]
    sql = _compiled(stmt)
    assert "(gait_records.created_at, gait_records.id) >" in sql
    assert str(LAST_ID) in sql
