"""진료비 기록 (#353). SQL 이 원본이고 모델이 따라간다.

아래쪽 리포지토리 테스트는 `fakes.py` 를 안 건드린다 — #331 과 같은 파일을 만지지
않으려는 것과 같은 이유다 (task-2 지시). `care_event.py` 처럼 함수 전체를 갈아 끼우는
대역이 아니라, `vet_visit.py` 가 실제로 만드는 `select()` 를 해석해서 리스트를 거르는
얇은 세션 대역을 쓴다 — 그래야 WHERE·ORDER BY·LIMIT 이 진짜로 맞는지 이 테스트가 본다.
"""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from daengs_backend.models import VET_REASON_CODES, VetVisit, VetVisitDraft
from daengs_backend.repositories import vet_visit as vet_repo

_SQL = Path(__file__).resolve().parents[2] / "db" / "init" / "25_vet_visits.sql"


def _codes_in_check(constraint: str) -> tuple[str, ...]:
    """`db/init` 의 CHECK 정의문에서 코드 목록만 뽑는다."""
    text = _SQL.read_text(encoding="utf-8")
    start = text.index(constraint)
    body = text[start : text.index("))", start)]
    return tuple(re.findall(r"'([a-z_]+)'", body))


def test_reason_codes_match_sql():
    """**이 테스트가 이 파일의 존재 이유다.** 모델과 SQL 이 갈리면 앱이 보내는 코드가
    DB CHECK 에서 터지는데, 그때 나오는 것은 500 이고 무엇이 어긋났는지 안 보인다."""
    assert VET_REASON_CODES == _codes_in_check("vet_visits_reason_code_check")


def test_suggested_reason_codes_match_sql():
    """제안 칸의 목록도 같아야 한다 — 둘이 갈리면 제안이 확정에서 떨어진다."""
    assert _codes_in_check("vet_visits_suggested_reason_code_check") == VET_REASON_CODES


def test_reason_codes_have_no_pathology_or_acuity():
    """축이 하나여야 한다 (verify ⑦ 과 같은 단언, 코드 쪽에서 한 번 더)."""
    for banned in ("tumor", "injury", "parasite", "emergency"):
        assert banned not in VET_REASON_CODES


# ---------------------------------------------------------------------------
# repositories/vet_visit.py — 쿼리 규칙
# ---------------------------------------------------------------------------

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
PET = uuid.uuid4()


class _VetStore:
    """가짜 저장소. `vet_visits` · `vet_visit_drafts` 두 표뿐이다."""

    def __init__(self) -> None:
        self.vet_visits: list[VetVisit] = []
        self.vet_visit_drafts: list[VetVisitDraft] = []

    def rows_for(self, table_name: str) -> list:
        return {"vet_visits": self.vet_visits, "vet_visit_drafts": self.vet_visit_drafts}[
            table_name
        ]


class _Scalars:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    """`vet_visit.py` 가 실제로 만드는 `select()` 를 해석해서 `_VetStore` 를 거른다.

    `AsyncSession` 을 흉내 낸 것이 아니라, 이 리포지토리가 실제로 쓰는 모양
    (동등·범위 비교 AND, order_by, limit, 사유별 group by 하나) 만큼만 이해한다.
    쿼리가 이 모양을 벗어나면 여기서 바로 `AttributeError`/`KeyError` 로 죽는다 —
    그것도 "쿼리가 바뀌었는데 테스트가 그걸 못 본다"를 막는 값이다.
    """

    def __init__(self, store: _VetStore) -> None:
        self.store = store

    def add(self, row: VetVisit | VetVisitDraft) -> None:
        if isinstance(row, VetVisit):
            self.store.vet_visits.append(row)
        elif isinstance(row, VetVisitDraft):
            self.store.vet_visit_drafts.append(row)
        else:
            raise TypeError(f"unexpected row type {type(row)}")

    async def delete(self, row: VetVisit | VetVisitDraft) -> None:
        if row in self.store.vet_visits:
            self.store.vet_visits.remove(row)
        elif row in self.store.vet_visit_drafts:
            self.store.vet_visit_drafts.remove(row)

    async def scalar(self, stmt):
        rows = self._select(stmt)
        return rows[0] if rows else None

    async def scalars(self, stmt):
        return _Scalars(self._select(stmt))

    async def execute(self, stmt):
        return _Result(self._group_by(stmt))

    @staticmethod
    def _matches(row, whereclause) -> bool:
        if whereclause is None:
            return True
        clauses = (
            whereclause.clauses
            if type(whereclause).__name__ == "BooleanClauseList"
            else [whereclause]
        )
        for clause in clauses:
            if not clause.operator(getattr(row, clause.left.key), clause.right.value):
                return False
        return True

    @staticmethod
    def _sort_key(clause):
        if hasattr(clause, "element"):
            return clause.element.key, getattr(clause.modifier, "__name__", "") == "desc_op"
        return clause.key, False

    def _select(self, stmt) -> list:
        table_name = stmt.get_final_froms()[0].name
        rows = [r for r in self.store.rows_for(table_name) if self._matches(r, stmt.whereclause)]
        for clause in reversed(stmt._order_by_clauses):
            key, desc = self._sort_key(clause)
            rows.sort(key=lambda r, k=key: getattr(r, k), reverse=desc)
        limit = stmt._limit_clause
        if limit is not None:
            rows = rows[: limit.value]
        return rows

    def _group_by(self, stmt) -> list[tuple]:
        """`sum_by_reason` 하나만 위한 좁은 해석 — 사유코드로 묶어 금액을 더한다."""
        table_name = stmt.get_final_froms()[0].name
        rows = [r for r in self.store.rows_for(table_name) if self._matches(r, stmt.whereclause)]
        group_key = next(iter(stmt.selected_columns)).key
        sum_col = next(iter(list(stmt.selected_columns)[1].clauses)).key
        totals: dict[str, int] = {}
        for row in rows:
            k = getattr(row, group_key)
            totals[k] = totals.get(k, 0) + getattr(row, sum_col)
        return list(totals.items())


def _visit(
    *, app_user_id=OWNER, pet_id=PET, reason="skin", total=80000, day=date(2026, 9, 2), **kw
):
    return VetVisit(
        id=uuid.uuid4(),
        app_user_id=app_user_id,
        pet_id=pet_id,
        visited_on=day,
        total_krw=total,
        reason_code=reason,
        client_event_id=uuid.uuid4(),
        **kw,
    )


def _draft(*, app_user_id=OWNER, pet_id=PET, sha=None, created_at=None, **kw):
    return VetVisitDraft(
        id=uuid.uuid4(),
        app_user_id=app_user_id,
        pet_id=pet_id,
        receipt_image_key="receipts/x.jpg",
        client_event_id=uuid.uuid4(),
        receipt_sha256=sha,
        created_at=created_at or datetime.now(UTC),
        **kw,
    )


async def test_add_appends_to_the_matching_table():
    store = _VetStore()
    session = _FakeSession(store)
    visit, draft = _visit(), _draft()
    vet_repo.add(session, visit)
    vet_repo.add(session, draft)
    assert store.vet_visits == [visit]
    assert store.vet_visit_drafts == [draft]


async def test_sum_by_reason_groups_and_totals():
    """사유별 누계 — 이 기능의 존재 이유다."""
    store = _VetStore()
    store.vet_visits += [
        _visit(reason="skin", total=80000),
        _visit(reason="skin", total=240000),
        _visit(reason="vaccination", total=80000),
    ]
    got = await vet_repo.sum_by_reason(
        _FakeSession(store), OWNER, PET, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert got == {"skin": 320000, "vaccination": 80000}


async def test_sum_by_reason_excludes_other_pets_and_other_users():
    """남의 강아지·남의 계정 기록이 누계에 섞이면 안 된다."""
    store = _VetStore()
    mine = _visit(reason="skin", total=80000)
    other_pet = _visit(reason="skin", total=999999, pet_id=uuid.uuid4())
    other_user = _visit(reason="skin", total=999999, app_user_id=STRANGER)
    store.vet_visits += [mine, other_pet, other_user]
    got = await vet_repo.sum_by_reason(
        _FakeSession(store), OWNER, PET, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert got == {"skin": 80000}


async def test_sum_by_reason_respects_date_range():
    store = _VetStore()
    inside = _visit(total=1000, day=date(2026, 6, 1))
    outside = _visit(total=2000, day=date(2025, 1, 1))
    store.vet_visits += [inside, outside]
    got = await vet_repo.sum_by_reason(
        _FakeSession(store), OWNER, PET, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert got == {"skin": 1000}


async def test_list_between_orders_most_recent_first():
    store = _VetStore()
    old = _visit(day=date(2026, 1, 1))
    new = _visit(day=date(2026, 9, 1))
    outside = _visit(day=date(2027, 1, 1))
    store.vet_visits += [old, new, outside]
    got = await vet_repo.list_between(
        _FakeSession(store), OWNER, PET, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert got == [new, old]


async def test_get_by_client_event_scoped_to_owner():
    """멱등키는 `(app_user_id, client_event_id)` 단위다 — 남의 것과 안 겹쳐야 한다."""
    store = _VetStore()
    mine = _visit()
    store.vet_visits.append(mine)
    session = _FakeSession(store)
    got = await vet_repo.get_by_client_event(session, OWNER, mine.client_event_id)
    miss = await vet_repo.get_by_client_event(session, STRANGER, mine.client_event_id)
    assert got is mine
    assert miss is None


async def test_find_duplicate_matches_day_and_total():
    """같은 날 같은 금액 — possible_duplicate 의 근거."""
    store = _VetStore()
    store.vet_visits.append(_visit(day=date(2026, 9, 2), total=80000))
    session = _FakeSession(store)
    hit = await vet_repo.find_duplicate(session, OWNER, PET, date(2026, 9, 2), 80000)
    miss = await vet_repo.find_duplicate(session, OWNER, PET, date(2026, 9, 2), 80001)
    assert hit is not None
    assert miss is None


async def test_get_draft_owned_excludes_stranger():
    store = _VetStore()
    draft = _draft()
    store.vet_visit_drafts.append(draft)
    session = _FakeSession(store)
    assert await vet_repo.get_draft_owned(session, OWNER, draft.id) is draft
    assert await vet_repo.get_draft_owned(session, STRANGER, draft.id) is None


async def test_get_draft_by_sha_returns_most_recent():
    """지우고 다시 올리는 것이 정당하다 — UNIQUE 가 아니라 최근 것을 돌려준다."""
    store = _VetStore()
    older = _draft(sha="a" * 64, created_at=datetime(2026, 9, 1, tzinfo=UTC))
    newer = _draft(sha="a" * 64, created_at=datetime(2026, 9, 2, tzinfo=UTC))
    store.vet_visit_drafts += [older, newer]
    got = await vet_repo.get_draft_by_sha(_FakeSession(store), OWNER, "a" * 64)
    assert got is newer


async def test_delete_draft_removes_it():
    store = _VetStore()
    draft = _draft()
    store.vet_visit_drafts.append(draft)
    session = _FakeSession(store)
    await vet_repo.delete_draft(session, draft)
    assert draft not in store.vet_visit_drafts


async def test_expired_drafts_respects_limit_and_oldest_first():
    """청소는 요청당 최대 `limit` 건 — 한 요청이 몇 천 건을 지우지 않는다."""
    store = _VetStore()
    now = datetime.now(UTC)
    old = [_draft(created_at=now - timedelta(hours=30 + i)) for i in range(60)]
    fresh = _draft(created_at=now - timedelta(minutes=1))
    store.vet_visit_drafts += old + [fresh]
    got = await vet_repo.expired_drafts(_FakeSession(store), now - timedelta(hours=24), limit=50)
    assert len(got) == 50
    assert fresh not in got
    # 가장 오래된 것부터 지운다 — 순서가 아니라 상한을 시험하는 테스트가 놓치는 지점.
    assert got[0].created_at < got[-1].created_at
