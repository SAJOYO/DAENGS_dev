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
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.core.storage import LocalBridgeStorage
from daengs_backend.models import VET_REASON_CODES, VET_REASON_LABELS, VetVisit, VetVisitDraft
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import vet_visit as vet_repo
from daengs_backend.routers import vet_visit as vet_router
from daengs_backend.schemas.vet_visit import VetVisitConfirmRequest
from daengs_backend.services import vet_receipt
from daengs_backend.services import vet_visit as vet_service
from daengs_backend.services.vet_receipt import (
    ReceiptExtraction,
    ReceiptExtractionFailed,
    ReceiptItem,
)

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


# ---------------------------------------------------------------------------
# services/vet_visit.py — 동의 분기 · 멱등 세 층 · 초안 청소
# ---------------------------------------------------------------------------
#
# 리포지토리를 진짜 SQL 대역(`_FakeSession`)이 아니라 dict 기반 대역으로 갈아 끼운다
# (`care_event.py`/`screening.py` 테스트와 같은 결) — 여기서 보고 싶은 것은 서비스의
# 판단(동의·멱등·트랜잭션 경계)이고, 위쪽 리포지토리 테스트가 이미 쿼리 모양을 본다.
# 저장소는 진짜 `LocalBridgeStorage`(임시 디렉터리)를 쓴다 — create-only·바이트 읽기가
# 구현에 붙어 있는 성질이라 가짜로 바꾸면 보고 싶은 것이 안 보인다 (test_screening_records.py 주석).

SVC_OWNER = uuid.uuid4()
SVC_PET = uuid.uuid4()

JPEG = "image/jpeg"

_OK_EXTRACTION = ReceiptExtraction(
    status="ok",
    visited_on=date(2026, 9, 2),
    total_krw=80000,
    hospital_name="○○동물병원",
    items=[ReceiptItem(name="초진료", amount_krw=80000)],
    suggested_reason_code="skin",
)


def _unreadable_extract(_bytes, _content_type):
    async def _inner():
        return ReceiptExtraction(status="unreadable", unreadable_reason="blurry")

    return _inner()


def _raising_extract(exc):
    def _fn(_bytes, _content_type):
        async def _inner():
            raise exc

        return _inner()

    return _fn


def _counting_extract(calls, extraction=_OK_EXTRACTION):
    def _fn(image_bytes, content_type):
        async def _inner():
            calls.append((image_bytes, content_type))
            return extraction

        return _inner()

    return _fn


class _NullSession:
    """`vet_repo` 를 통째로 대역으로 갈아 끼웠으므로 세션 자신은 commit/rollback 만
    필요하다."""

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest.fixture
def svc_store(monkeypatch: pytest.MonkeyPatch):
    drafts: dict[uuid.UUID, VetVisitDraft] = {}
    visits: dict[uuid.UUID, VetVisit] = {}
    pets: dict[uuid.UUID, object] = {SVC_PET: SimpleNamespace(id=SVC_PET, app_user_id=SVC_OWNER)}
    app_users: dict[uuid.UUID, object] = {}

    def add(_session, row):
        if isinstance(row, VetVisitDraft):
            if row.created_at is None:
                row.created_at = datetime.now(UTC)
            drafts[row.id] = row
        else:
            if row.id is None:
                row.id = uuid.uuid4()
            if row.created_at is None:
                row.created_at = datetime.now(UTC)
            visits[row.id] = row

    async def delete_draft(_session, draft):
        drafts.pop(draft.id, None)

    async def get_draft_by_client_event(_session, app_user_id, client_event_id):
        return next(
            (
                d
                for d in drafts.values()
                if d.app_user_id == app_user_id and d.client_event_id == client_event_id
            ),
            None,
        )

    async def get_draft_owned(_session, app_user_id, draft_id):
        d = drafts.get(draft_id)
        return d if d is not None and d.app_user_id == app_user_id else None

    async def find_draft_by_image_key(_session, storage_key):
        """bridge 전용 — 소유자 조건이 없다(`vet_repo.find_draft_by_image_key` 와 같다)."""
        return next((d for d in drafts.values() if d.receipt_image_key == storage_key), None)

    async def get_draft_by_sha(_session, app_user_id, sha256_hex):
        matches = [
            d
            for d in drafts.values()
            if d.app_user_id == app_user_id and d.receipt_sha256 == sha256_hex
        ]
        return max(matches, key=lambda d: d.created_at, default=None)

    async def expired_drafts(_session, before, limit=50):
        old = sorted(
            (d for d in drafts.values() if d.created_at < before), key=lambda d: d.created_at
        )
        return old[:limit]

    async def find_duplicate(_session, app_user_id, pet_id, visited_on, total_krw):
        return next(
            (
                v
                for v in visits.values()
                if v.app_user_id == app_user_id
                and v.pet_id == pet_id
                and v.visited_on == visited_on
                and v.total_krw == total_krw
            ),
            None,
        )

    async def get_many_by_client_events(_session, app_user_id, client_event_ids):
        wanted = set(client_event_ids)
        return {
            v.client_event_id: v
            for v in visits.values()
            if v.app_user_id == app_user_id and v.client_event_id in wanted
        }

    async def count_by_image_key(_session, storage_key):
        return sum(1 for v in visits.values() if v.receipt_image_key == storage_key)

    async def get_by_client_event(_session, app_user_id, client_event_id):
        return next(
            (
                v
                for v in visits.values()
                if v.app_user_id == app_user_id and v.client_event_id == client_event_id
            ),
            None,
        )

    async def get_owned_visit(_session, app_user_id, visit_id):
        v = visits.get(visit_id)
        return v if v is not None and v.app_user_id == app_user_id else None

    async def delete_visit_row(_session, visit):
        visits.pop(visit.id, None)

    async def count_before(_session, app_user_id, pet_id, before):
        return sum(
            1
            for v in visits.values()
            if v.app_user_id == app_user_id and v.pet_id == pet_id and v.visited_on < before
        )

    async def list_between(_session, app_user_id, pet_id, start, end):
        matched = [
            v
            for v in visits.values()
            if v.app_user_id == app_user_id and v.pet_id == pet_id and start <= v.visited_on <= end
        ]
        return sorted(matched, key=lambda v: v.visited_on, reverse=True)

    async def get_owned_pet(_session, app_user_id, pet_id):
        pet = pets.get(pet_id)
        return pet if pet is not None and pet.app_user_id == app_user_id else None

    async def get_app_user(_session, app_user_id):
        return app_users.get(app_user_id)

    monkeypatch.setattr(vet_repo, "add", add)
    monkeypatch.setattr(vet_repo, "delete_draft", delete_draft)
    monkeypatch.setattr(vet_repo, "get_draft_by_client_event", get_draft_by_client_event)
    monkeypatch.setattr(vet_repo, "get_draft_owned", get_draft_owned)
    monkeypatch.setattr(vet_repo, "find_draft_by_image_key", find_draft_by_image_key)
    monkeypatch.setattr(vet_repo, "get_draft_by_sha", get_draft_by_sha)
    monkeypatch.setattr(vet_repo, "expired_drafts", expired_drafts)
    monkeypatch.setattr(vet_repo, "find_duplicate", find_duplicate)
    monkeypatch.setattr(vet_repo, "get_by_client_event", get_by_client_event)
    monkeypatch.setattr(vet_repo, "get_many_by_client_events", get_many_by_client_events)
    monkeypatch.setattr(vet_repo, "count_by_image_key", count_by_image_key)
    monkeypatch.setattr(vet_repo, "get_owned", get_owned_visit)
    monkeypatch.setattr(vet_repo, "delete", delete_visit_row)
    monkeypatch.setattr(vet_repo, "list_between", list_between)
    monkeypatch.setattr(vet_repo, "count_before", count_before)
    monkeypatch.setattr(pet_repo, "get_owned", get_owned_pet)
    monkeypatch.setattr(app_user_repo, "get_by_id", get_app_user)

    return SimpleNamespace(drafts=drafts, visits=visits, pets=pets, app_users=app_users)


@pytest.fixture
def svc_storage(monkeypatch: pytest.MonkeyPatch, tmp_path):
    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    monkeypatch.setattr(vet_service, "get_storage", lambda: s)
    return s


@pytest.fixture
def svc_session():
    return _NullSession()


def _consent(store, *, at: datetime | None, version: str | None = "v1") -> None:
    store.app_users[SVC_OWNER] = SimpleNamespace(ocr_consent_at=at, ocr_consent_version=version)


async def _start(session, store, *, client_event_id=None):
    body = vet_service.StartDraftRequest(
        pet_id=SVC_PET, content_type=JPEG, client_event_id=client_event_id or uuid.uuid4()
    )
    return await vet_service.start_draft(session, SVC_OWNER, body)


def _upload(
    storage: LocalBridgeStorage, draft: VetVisitDraft, data: bytes = b"receipt-bytes"
) -> None:
    storage.write(draft.receipt_image_key, data)


# ── 멱등 ① client_event_id ───────────────────────────────────────────


async def test_second_tap_returns_existing_draft_without_new_ticket(
    svc_session, svc_store, svc_storage
):
    """얼어 보이는 화면에서 두 번 탭 — Gemini 도 저장소도 다시 안 간다."""
    key = uuid.uuid4()
    draft1, _ticket1, created1 = await _start(svc_session, svc_store, client_event_id=key)
    draft2, _ticket2, created2 = await _start(svc_session, svc_store, client_event_id=key)
    assert created1 is True and created2 is False
    assert draft1.id == draft2.id
    assert len(svc_store.drafts) == 1


async def test_start_draft_rejects_pet_i_do_not_own(svc_session, svc_store, svc_storage):
    body = vet_service.StartDraftRequest(
        pet_id=uuid.uuid4(), content_type=JPEG, client_event_id=uuid.uuid4()
    )
    with pytest.raises(vet_service.VetVisitNotFoundError):
        await vet_service.start_draft(svc_session, SVC_OWNER, body)


# ── 초안 청소 ──────────────────────────────────────────────────────────


async def test_start_draft_sweeps_expired(svc_session, svc_store, svc_storage):
    """청소는 초안을 만들 때 같이 간다 (Beat 가 없다) — 요청당 최대 50건, 사진도 같이."""
    now = datetime.now(UTC)
    for i in range(60):
        old = VetVisitDraft(
            id=uuid.uuid4(),
            app_user_id=SVC_OWNER,
            pet_id=SVC_PET,
            receipt_image_key=f"vet-receipts/{SVC_OWNER}/old-{i}/receipt.jpg",
            client_event_id=uuid.uuid4(),
            created_at=now - timedelta(hours=30 + i),
        )
        svc_storage.write(old.receipt_image_key, b"x")
        svc_store.drafts[old.id] = old
    await _start(svc_session, svc_store)
    assert len(svc_store.drafts) == 11  # 60건 중 50건이 쓸리고 새 것 하나
    # 지운 초안의 사진도 같이 지운다 — 가장 오래된 것(old-59)은 확실히 지워진다.
    assert not svc_storage.local_path(f"vet-receipts/{SVC_OWNER}/old-59/receipt.jpg").exists()


# ── 동의 분기 (extract 에서 갈린다) ────────────────────────────────────


async def test_no_consent_drops_items_from_draft(svc_session, svc_store, svc_storage, monkeypatch):
    """**동의 분기는 초안을 쓸 때다.** 미동의면 items 가 DB 에 안 앉는다."""
    _consent(svc_store, at=None, version=None)
    draft, _ticket, _created = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    result = await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    assert "items" not in draft.extracted
    # 응답에는 그대로 실려 있다 — 화면은 손해를 안 본다.
    assert result.extraction.items


async def test_consent_keeps_items_in_draft(svc_session, svc_store, svc_storage, monkeypatch):
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft, _ticket, _created = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    assert draft.extracted["items"]


# ── 멱등 ③ extracted_at ───────────────────────────────────────────────


async def test_extract_twice_does_not_call_gemini_again(
    svc_session, svc_store, svc_storage, monkeypatch
):
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft, _ticket, _created = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    calls: list = []
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract(calls))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    assert len(calls) == 1


# ── 멱등 ② receipt_sha256 ─────────────────────────────────────────────


async def test_same_photo_new_draft_skips_gemini(svc_session, svc_store, svc_storage, monkeypatch):
    """앱이 재시작해 새 키로 같은 사진을 올린다 — 추출 **전에** sha256 으로 잡는다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft1, _t1, _c1 = await _start(svc_session, svc_store)
    _upload(svc_storage, draft1, b"same-bytes")
    calls: list = []
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract(calls))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft1.id)
    assert len(calls) == 1

    draft2, _t2, _c2 = await _start(svc_session, svc_store)
    _upload(svc_storage, draft2, b"same-bytes")
    result2 = await vet_service.extract_draft(svc_session, SVC_OWNER, draft2.id)
    assert len(calls) == 1  # Gemini 재호출 없음
    assert result2.status == "ok"
    assert draft2.extracted_at is not None


async def test_revoked_consent_still_strips_items_on_sha_reuse_path(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """Fix round 1, Finding 1 — 동의 O 로 추출(항목 저장) → 동의 철회 → 같은 사진을
    새 초안으로 재업로드(멱등 ②, sha256 재사용). 재사용 경로도 **현재** 동의를 봐야
    한다 — 옛 초안에 있던 항목을 그대로 베끼면 학습 코퍼스로 새는 문이 열린다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft1, _t1, _c1 = await _start(svc_session, svc_store)
    _upload(svc_storage, draft1, b"same-bytes")
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft1.id)
    assert draft1.extracted["items"]  # 동의 상태였으니 저장됐다

    _consent(svc_store, at=None, version=None)  # 철회
    draft2, _t2, _c2 = await _start(svc_session, svc_store)
    _upload(svc_storage, draft2, b"same-bytes")
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft2.id)
    assert "items" not in draft2.extracted

    [visit] = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft2.id, _confirm_body())
    assert visit.raw_ocr_items == []


# ── 못 읽었을 때 — 500 이 아니다 ───────────────────────────────────────


async def test_unreadable_extraction_is_not_an_error(
    svc_session, svc_store, svc_storage, monkeypatch
):
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _unreadable_extract)
    result = await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    assert result.status == "unreadable"
    assert result.unreadable_reason == "blurry"


async def test_gemini_failure_becomes_failed_not_500(
    svc_session, svc_store, svc_storage, monkeypatch
):
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _raising_extract(ReceiptExtractionFailed("boom")))
    result = await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    assert result.status == "failed"
    assert draft.extracted_at is None  # 저장 안 함 — 다시 시도할 수 있다


# ── possible_duplicate ─────────────────────────────────────────────────


async def test_possible_duplicate_true_when_confirmed_match_exists(
    svc_session, svc_store, svc_storage, monkeypatch
):
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    existing = VetVisit(
        app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2026, 9, 2), total_krw=80000,
        reason_code="skin", client_event_id=uuid.uuid4(),
    )
    svc_store.visits[uuid.uuid4()] = existing
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    result = await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    assert result.possible_duplicate is True


async def test_possible_duplicate_recomputed_on_sha_reuse_path(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """M3 — real bug. 사진 재사용(멱등 ②) 경로는 예전에 `match.extracted` 를 통째로
    베껴 `possible_duplicate` 가 그때 값에 갇혔다. 매칭되는 방문이 그 뒤에 확정돼도
    안 켜졌다 — 이 테스트가 그 회귀를 막는다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))

    # draft1 — 추출은 되지만 확정 안 하고 남긴다. 나중에 sha 재사용의 매치가 된다.
    draft1, _t1, _c1 = await _start(svc_session, svc_store)
    _upload(svc_storage, draft1, b"same-bytes")
    result1 = await vet_service.extract_draft(svc_session, SVC_OWNER, draft1.id)
    assert result1.possible_duplicate is False  # 아직 매칭되는 확정 기록이 없다

    # draft1 과 무관한 다른 초안을 확정해 (pet_id, visited_on, total_krw) 매치를 만든다.
    other, _t2, _c2 = await _start(svc_session, svc_store)
    _upload(svc_storage, other, b"different-bytes")
    await vet_service.extract_draft(svc_session, SVC_OWNER, other.id)
    await vet_service.confirm_draft(svc_session, SVC_OWNER, other.id, _confirm_body())

    # 같은 사진을 다시 올린다 — draft1 이 멱등 ② 로 매치된다(사진과 함께 재사용).
    draft2, _t3, _c3 = await _start(svc_session, svc_store)
    _upload(svc_storage, draft2, b"same-bytes")
    result2 = await vet_service.extract_draft(svc_session, SVC_OWNER, draft2.id)
    assert result2.possible_duplicate is True  # 그 사이 확정된 매치를 다시 잰다


# ── confirm — items 는 초안에서만 ───────────────────────────────────────


def _confirm_body(**kw) -> "vet_service.ConfirmDraftRequest":
    """한 마리 확정 = `splits` 하나. **한 마리가 특수 케이스가 아니라 N=1 이다.**"""
    splits = kw.pop("splits", None)
    if splits is None:
        # 한 마리 — 확정 값이 영수증 값과 같다. 그래서 검산이 공짜로 통과한다.
        split_keys = (
            "client_event_id", "reason_code", "reason_detail",
            "pet_id", "is_emergency", "is_oncology", "patient_index",
        )
        split_kw = {k: kw.pop(k) for k in list(kw) if k in split_keys}
        defaults = {"visited_on": date(2026, 9, 2), "total_krw": 80000}
        defaults.update(kw)
        split = {
            "client_event_id": uuid.uuid4(),
            "reason_code": "cardiac",
            "total_krw": defaults["total_krw"],
        }
        split.update(split_kw)
        return vet_service.ConfirmDraftRequest(
            splits=(vet_service.ConfirmSplit(**split),), **defaults
        )
    # splits 를 직접 준 경우 kw 는 전부 영수증 단위다 (total_krw 포함).
    defaults = {"visited_on": date(2026, 9, 2), "total_krw": 80000}
    defaults.update(kw)
    return vet_service.ConfirmDraftRequest(splits=tuple(splits), **defaults)


async def test_confirm_reads_items_from_draft(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """`raw_ocr_items` 는 초안에서만 온다 (`ConfirmDraftRequest` 에는 `items` 를 받는
    필드가 없다 — L5, 요청 본문을 믿으면 앱이 동의 분기를 우회한다)."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    stored_items = draft.extracted["items"]

    body = _confirm_body()
    [visit] = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert visit.raw_ocr_items == stored_items


async def test_confirm_without_consent_stores_empty_items(
    svc_session, svc_store, svc_storage, monkeypatch
):
    _consent(svc_store, at=None, version=None)
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)

    body = _confirm_body()
    [visit] = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert visit.raw_ocr_items == []


async def test_confirm_records_suggested_code_for_comparison(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """제안과 확정을 둘 다 남겨야 "받아들였나 고쳤나" 가 나온다 (label_source 없는 이유)."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)

    body = _confirm_body(reason_code="cardiac")
    [visit] = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert visit.suggested_reason_code == "skin"
    assert visit.reason_code == "cardiac"


async def test_confirm_deletes_draft_but_keeps_the_photo(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """확정돼도 사진 키가 안 바뀐다 — 초안 행만 지우고 객체는 그대로 물려준다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    key = draft.receipt_image_key

    [visit] = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, _confirm_body())
    assert draft.id not in svc_store.drafts
    assert visit.receipt_image_key == key
    assert svc_storage.local_path(key).exists()


async def test_confirm_is_idempotent_on_its_own_client_event_id(
    svc_session, svc_store, svc_storage, monkeypatch
):
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)

    body = _confirm_body()
    [first] = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert draft.id not in svc_store.drafts
    # 두 번째는 draft 가 이미 지워졌어도 vet_visits 자신의 멱등키로 잡힌다 — 404 가 아니다.
    [second] = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert second is first
    assert len(svc_store.visits) == 1


# ── 다견 영수증 — 한 장이 아이별 행 N개가 된다 ────────────────────────


SECOND_PET = uuid.uuid4()


def _add_second_pet(svc_store):
    svc_store.pets[SECOND_PET] = SimpleNamespace(id=SECOND_PET, app_user_id=SVC_OWNER)


def _two_splits(total_a=109_200, total_b=82_100):
    """실측 다견 영수증의 두 블록 (2026-09-14). 합이 정확히 191,300 이다."""
    return (
        vet_service.ConfirmSplit(
            client_event_id=uuid.uuid4(),
            reason_code="ear",
            total_krw=total_a,
            patient_index=0,
        ),
        vet_service.ConfirmSplit(
            client_event_id=uuid.uuid4(),
            pet_id=SECOND_PET,
            reason_code="skin",
            total_krw=total_b,
            patient_index=1,
        ),
    )


async def _extracted_draft(svc_session, svc_store, svc_storage, monkeypatch, extraction=None):
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    if extraction is None:
        monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    else:
        monkeypatch.setattr(vet_receipt, "extract", _counting_extract([], extraction))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    return draft


async def test_one_receipt_becomes_one_row_per_pet(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """실측 다견 영수증 그대로 — 191,300 이 109,200 + 82,100 으로 갈린다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    _add_second_pet(svc_store)
    draft = await _extracted_draft(svc_session, svc_store, svc_storage, monkeypatch)

    body = _confirm_body(total_krw=191_300, splits=_two_splits())
    visits = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)

    assert len(visits) == 2
    assert [v.total_krw for v in visits] == [109_200, 82_100]
    assert {v.pet_id for v in visits} == {SVC_PET, SECOND_PET}
    assert {v.visited_on for v in visits} == {date(2026, 9, 2)}


async def test_splits_that_do_not_add_up_are_rejected(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """합이 영수증 총액과 다르면 확정 자체를 막는다 — 조용히 틀린 누계보다 낫다.

    **이 검사는 items 를 안 쓴다**(요청 안에서 닫힌다). 그래서 OCR 학습 미동의
    유저에게도 똑같이 돈다 — 미동의면 초안에 items 가 없다 (docs §3).
    """
    _consent(svc_store, at=None, version=None)
    _add_second_pet(svc_store)
    draft = await _extracted_draft(svc_session, svc_store, svc_storage, monkeypatch)

    body = _confirm_body(total_krw=191_300, splits=_two_splits(total_b=82_099))
    with pytest.raises(vet_service.VetSplitSumError):
        await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert svc_store.visits == {}


async def test_split_rejects_a_pet_i_do_not_own(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """**이 PR 에서 소유권 검사가 처음 생기는 자리다.** 예전에는 pet_id 가 초안에서
    와서 `start_draft` 의 검사 하나로 충분했는데, splits 는 앱이 보낸다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft = await _extracted_draft(svc_session, svc_store, svc_storage, monkeypatch)

    mine = vet_service.ConfirmSplit(
        client_event_id=uuid.uuid4(), reason_code="ear", total_krw=20000, patient_index=0
    )
    stranger = vet_service.ConfirmSplit(
        client_event_id=uuid.uuid4(),
        pet_id=uuid.uuid4(),
        reason_code="skin",
        total_krw=80000,
        patient_index=1,
    )
    body = _confirm_body(total_krw=100_000, splits=(mine, stranger))
    with pytest.raises(vet_service.VetVisitNotFoundError):
        await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)


async def test_items_are_cut_by_block_not_copied_to_every_row(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """항목을 통째로 복사하면 학습 코퍼스에 잘못 라벨된 데이터가 N배로 쌓인다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    _add_second_pet(svc_store)
    extraction = _OK_EXTRACTION.model_copy(
        update={
            "patient_count": 2,
            # `model_copy` 는 검증을 안 하므로 직렬화 경고가 안 나게 모델로 넣는다.
            "items": [
                ReceiptItem(name="*검사-귀-도말", amount_krw=20000, patient_index=0),
                ReceiptItem(name="소염위생관리", amount_krw=15000, patient_index=1),
                ReceiptItem(name="어느 블록인지 모를 항목", amount_krw=1000),
            ],
        }
    )
    draft = await _extracted_draft(
        svc_session, svc_store, svc_storage, monkeypatch, extraction=extraction
    )

    body = _confirm_body(total_krw=191_300, splits=_two_splits())
    first, second = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)

    assert [i["name"] for i in first.raw_ocr_items] == ["*검사-귀-도말"]
    assert [i["name"] for i in second.raw_ocr_items] == ["소염위생관리"]
    # 어느 블록인지 모를 항목은 **버린다** — 아무 아이에게나 붙이면 코퍼스가 틀린다.
    assert all(
        "모를" not in i["name"] for v in (first, second) for i in v.raw_ocr_items
    )


async def test_suggested_code_is_null_when_the_receipt_is_split(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """추출의 제안은 **영수증 하나당 하나**라 아이별 제안이 아니다. N행에 복사하면
    `suggested_reason_code` 와 `reason_code` 의 비교가 거짓이 되는데, `label_source`
    칸을 안 둔 설계가 바로 그 비교에 기대고 있다 (25_vet_visits.sql:64-66)."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    _add_second_pet(svc_store)
    draft = await _extracted_draft(svc_session, svc_store, svc_storage, monkeypatch)
    assert draft.extracted["suggested_reason_code"] == "skin"  # 제안은 분명히 있었다

    body = _confirm_body(total_krw=191_300, splits=_two_splits())
    visits = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert [v.suggested_reason_code for v in visits] == [None, None]


async def test_single_split_still_records_the_suggestion(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """한 마리면 제안이 그 아이 것이 맞다 — 비교가 살아 있어야 한다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft = await _extracted_draft(svc_session, svc_store, svc_storage, monkeypatch)
    [visit] = await vet_service.confirm_draft(
        svc_session, SVC_OWNER, draft.id, _confirm_body()
    )
    assert visit.suggested_reason_code == "skin"


async def test_split_confirm_deletes_the_draft_exactly_once(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """**불변식이다.** 초안을 남기면 24시간 뒤 청소가 확정된 기록들의 사진을 지운다
    (`_sweep_expired` 주석)."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    _add_second_pet(svc_store)
    draft = await _extracted_draft(svc_session, svc_store, svc_storage, monkeypatch)
    key = draft.receipt_image_key

    body = _confirm_body(total_krw=191_300, splits=_two_splits())
    visits = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)

    assert draft.id not in svc_store.drafts
    assert {v.receipt_image_key for v in visits} == {key}  # 같은 사진을 공유한다
    assert svc_storage.local_path(key).exists()


async def test_deleting_one_pet_keeps_the_shared_photo(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """마지막 참조가 사라질 때만 사진을 지운다 — 아이 하나를 지울 때 나머지의 사진까지
    날아가면 안 된다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    _add_second_pet(svc_store)
    draft = await _extracted_draft(svc_session, svc_store, svc_storage, monkeypatch)
    key = draft.receipt_image_key
    first, second = await vet_service.confirm_draft(
        svc_session, SVC_OWNER, draft.id, _confirm_body(total_krw=191_300, splits=_two_splits())
    )

    await vet_service.delete_visit(svc_session, SVC_OWNER, first.id)
    assert svc_storage.local_path(key).exists(), "아직 둘째가 물고 있다"

    await vet_service.delete_visit(svc_session, SVC_OWNER, second.id)
    assert not svc_storage.local_path(key).exists(), "마지막 참조가 사라지면 지운다"


async def test_split_confirm_is_idempotent_on_every_key(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """재시도는 키별로 잡힌다 — 요청 단위 all-or-nothing 이라는 개념은 이 저장소에
    없다 (`care_event.create` · docs/walk/upload-idempotency.md)."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    _add_second_pet(svc_store)
    draft = await _extracted_draft(svc_session, svc_store, svc_storage, monkeypatch)

    body = _confirm_body(total_krw=191_300, splits=_two_splits())
    first = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    # 초안은 이미 지워졌다. 그래도 키가 전부 있으므로 404 가 아니라 같은 행을 돌려준다.
    second = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert [v.id for v in second] == [v.id for v in first]
    assert len(svc_store.visits) == 2


# ── 확인 화면이 블록을 볼 수 있나 ────────────────────────────────────


def test_extract_response_carries_the_blocks_to_the_app(app_client, svc_storage, monkeypatch):
    """**앱이 아이별 분할을 제안할 유일한 재료다.** 추출이 블록을 읽어 초안에 넣어도
    응답이 그것을 안 나르면 확인 화면은 아무것도 못 한다 — 고른 한 아이에게 전액이
    붙고, 금액도 병원도 맞아서 화면은 정상으로 보인다 (#536 머지 뒤 발견)."""
    extraction = _OK_EXTRACTION.model_copy(
        update={
            "patient_count": 2,
            "total_krw": 191_300,
            "items": [
                ReceiptItem(name="*검사-귀-도말", amount_krw=20000, patient_index=0),
                ReceiptItem(name="소염위생관리", amount_krw=15000, patient_index=1),
            ],
        }
    )
    _started, extracted = _extract(app_client, svc_storage, monkeypatch, extraction=extraction)
    body = extracted.json()

    assert body["patient_count"] == 2
    assert [i["patient_index"] for i in body["items"]] == [0, 1]


def test_extract_response_says_one_block_for_a_single_pet_receipt(
    app_client, svc_storage, monkeypatch
):
    """한 마리면 `patient_count == 1` 이고 인덱스는 전부 `null` — 앱이 분할을 안 묻는 근거."""
    _started, extracted = _extract(app_client, svc_storage, monkeypatch)
    body = extracted.json()

    assert body["patient_count"] == 1
    assert all(i["patient_index"] is None for i in body["items"])


# ── HTTP 경계의 구 모양 호환 ──────────────────────────────────────────


def _split_json(total_b=82_100):
    return {
        "visited_on": "2026-09-02",
        "total_krw": 191_300,
        "splits": [
            {
                "client_event_id": str(uuid.uuid4()),
                "reason_code": "ear",
                "total_krw": 109_200,
                "patient_index": 0,
            },
            {
                "client_event_id": str(uuid.uuid4()),
                "pet_id": str(SECOND_PET),
                "reason_code": "skin",
                "total_krw": total_b,
                "patient_index": 1,
            },
        ],
    }


def test_flat_body_still_returns_a_single_object(app_client, svc_storage, monkeypatch):
    """**구버전 앱이 깨지지 않는다.** 앱은 스토어를 거쳐 깔려서 한동안 남는다 —
    배열을 주면 그 자리에서 깨진다."""
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm", json=_confirm_json_body()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict)
    assert body["total_krw"] == 80000


def test_splits_body_returns_a_list(app_client, svc_store, svc_storage, monkeypatch):
    _add_second_pet(svc_store)
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm", json=_split_json()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert [v["total_krw"] for v in body] == [109_200, 82_100]


def test_splits_that_do_not_add_up_are_422(app_client, svc_store, svc_storage, monkeypatch):
    _add_second_pet(svc_store)
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm", json=_split_json(total_b=1)
    )
    assert r.status_code == 422


def test_empty_splits_is_422(app_client, svc_storage, monkeypatch):
    """N≥1 이다 — 아무 행도 안 만드는 확정은 확정이 아니다."""
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm",
        json={"visited_on": "2026-09-02", "total_krw": 0, "splits": []},
    )
    assert r.status_code == 422


# ── reason_options ───────────────────────────────────────────────────


async def test_reason_options_puts_recent_first_then_the_rest(svc_session, svc_store):
    """M4 — 코드만이 아니라 표시명도 같이 낸다 (`VET_REASON_LABELS`)."""
    svc_store.visits[uuid.uuid4()] = VetVisit(
        app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2026, 8, 1), total_krw=1000,
        reason_code="skin", client_event_id=uuid.uuid4(),
    )
    svc_store.visits[uuid.uuid4()] = VetVisit(
        app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2026, 9, 1), total_krw=1000,
        reason_code="cardiac", client_event_id=uuid.uuid4(),
    )
    options = await vet_service.reason_options(svc_session, SVC_OWNER, SVC_PET)
    codes = [o.code for o in options]
    assert codes[0] == "cardiac"  # 가장 최근
    assert codes[1] == "skin"
    assert set(codes) == set(VET_REASON_CODES)
    assert len(options) == len(VET_REASON_CODES)
    by_code = {o.code: o.label for o in options}
    assert by_code["skin"] == VET_REASON_LABELS["skin"]
    assert by_code["cardiac"] == VET_REASON_LABELS["cardiac"]


async def test_reason_options_rejects_pet_i_do_not_own(svc_session, svc_store):
    with pytest.raises(vet_service.VetVisitNotFoundError):
        await vet_service.reason_options(svc_session, SVC_OWNER, uuid.uuid4())


# ---------------------------------------------------------------------------
# routers/vet_visit.py + schemas/vet_visit.py — HTTP 경계 (task-5)
# ---------------------------------------------------------------------------
#
# 서비스 판단은 위에서 이미 봤다. 여기서 보는 것은 상태 코드다 — 멱등 ①의 201/200,
# 세 가지 추출 상태가 모두 200, 남의 초안은 404, 목록 밖의 사유는 422.
# `svc_store`·`svc_storage` 는 위 서비스 테스트와 같은 대역을 그대로 쓴다 —
# 라우터가 실제로 어떤 세션을 받아도 리포지토리가 그것을 건드리지 않는다
# (`test_care_events.py` 의 `client` 와 같은 결).

OTHER_OWNER = uuid.uuid4()


def _client(app_user_id: uuid.UUID) -> TestClient:
    app = FastAPI()
    app.include_router(vet_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=app_user_id)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def app_client(svc_store, svc_storage) -> TestClient:
    return _client(SVC_OWNER)


@pytest.fixture
def app_client_other(svc_store, svc_storage) -> TestClient:
    """남의 계정으로 같은 앱을 두드린다 — 존재하는 draft_id 라도 404 여야 한다."""
    return _client(OTHER_OWNER)


def _start_body(**kw) -> dict:
    body = {
        "pet_id": str(SVC_PET),
        "content_type": "image/jpeg",
        "client_event_id": str(uuid.uuid4()),
    }
    body.update(kw)
    return body


def _extract(
    app_client: TestClient, svc_storage: LocalBridgeStorage, monkeypatch, extraction=_OK_EXTRACTION
):
    """초안을 열고, 사진을 저장소에 직접 놓고(bridge 는 이 테스트의 관심사가 아니다),
    추출을 부른다. `(start_response.json(), extract_response)` 를 돌려준다."""
    started = app_client.post("/app/vet-visits", json=_start_body())
    assert started.status_code == 201, started.text
    started_body = started.json()
    svc_storage.write(started_body["storage_key"], b"receipt-bytes")
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([], extraction))
    extracted = app_client.post(f"/app/vet-visits/{started_body['draft_id']}/extract")
    return started_body, extracted


def _confirm_json_body(**kw) -> dict:
    body = {
        "client_event_id": str(uuid.uuid4()),
        "reason_code": "cardiac",
        "visited_on": "2026-09-02",
        "total_krw": 80000,
    }
    body.update(kw)
    return body


# ── 멱등 ①의 HTTP 표현 ───────────────────────────────────────────────


def test_second_post_returns_200_not_201(app_client):
    """멱등 ①의 HTTP 표현 — care_events·walks 와 같은 규칙."""
    body = _start_body()
    first = app_client.post("/app/vet-visits", json=body)
    second = app_client.post("/app/vet-visits", json=body)
    assert first.status_code == 201, first.text
    assert second.status_code == 200
    assert first.json()["draft_id"] == second.json()["draft_id"]


def test_start_draft_http_rejects_pet_i_do_not_own(app_client):
    r = app_client.post("/app/vet-visits", json=_start_body(pet_id=str(uuid.uuid4())))
    assert r.status_code == 404


# ── extract — 세 상태 모두 200, 남의 것은 404 ─────────────────────────


def test_draft_response_carries_possible_duplicate(app_client, svc_store, svc_storage, monkeypatch):
    svc_store.visits[uuid.uuid4()] = VetVisit(
        app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2026, 9, 2), total_krw=80000,
        reason_code="skin", client_event_id=uuid.uuid4(),
    )
    _started, extracted = _extract(app_client, svc_storage, monkeypatch)
    assert extracted.status_code == 200, extracted.text
    assert extracted.json()["possible_duplicate"] is True


def test_draft_response_carries_reason_options_recent_first(
    app_client, svc_store, svc_storage, monkeypatch
):
    """이 강아지가 실제로 겪은 사유가 맨 앞 — 적중률이 제일 높다."""
    svc_store.visits[uuid.uuid4()] = VetVisit(
        app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2026, 8, 1), total_krw=1000,
        reason_code="skin", client_event_id=uuid.uuid4(),
    )
    _started, extracted = _extract(app_client, svc_storage, monkeypatch)
    body = extracted.json()
    assert body["reason_options"][0] == {"code": "skin", "label": VET_REASON_LABELS["skin"]}
    assert {o["code"] for o in body["reason_options"]} == set(VET_REASON_CODES)


def test_extract_response_carries_extracted_fields_and_items(app_client, svc_storage, monkeypatch):
    _started, extracted = _extract(app_client, svc_storage, monkeypatch)
    body = extracted.json()
    assert body["extraction_status"] == "ok"
    assert body["visited_on"] == "2026-09-02"
    assert body["total_krw"] == 80000
    assert body["hospital_name"] == "○○동물병원"
    # `patient_index` 는 블록이 하나뿐이면 null 이다 — 앱이 분할을 안 묻는 근거.
    assert body["items"] == [{"name": "초진료", "amount_krw": 80000, "patient_index": None}]
    assert body["patient_count"] == 1
    assert body["suggested_reason_code"] == "skin"


def test_unreadable_returns_200_with_status(app_client, svc_storage, monkeypatch):
    unreadable = ReceiptExtraction(status="unreadable", unreadable_reason="blurry")
    _started, extracted = _extract(app_client, svc_storage, monkeypatch, extraction=unreadable)
    assert extracted.status_code == 200, extracted.text
    body = extracted.json()
    assert body["extraction_status"] == "unreadable"
    assert body["unreadable_reason"] == "blurry"
    assert body["items"] == []


def test_gemini_failure_is_200_not_500(app_client, svc_storage, monkeypatch):
    started = app_client.post("/app/vet-visits", json=_start_body())
    started_body = started.json()
    svc_storage.write(started_body["storage_key"], b"receipt-bytes")

    def _raise(_bytes, _content_type):
        async def _inner():
            raise ReceiptExtractionFailed("boom")

        return _inner()

    monkeypatch.setattr(vet_receipt, "extract", _raise)
    r = app_client.post(f"/app/vet-visits/{started_body['draft_id']}/extract")
    assert r.status_code == 200, r.text
    assert r.json()["extraction_status"] == "failed"


def test_other_users_draft_is_404(app_client, app_client_other, svc_storage, monkeypatch):
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client_other.post(f"/app/vet-visits/{started['draft_id']}/extract")
    assert r.status_code == 404


def test_unknown_draft_is_404(app_client):
    assert app_client.post(f"/app/vet-visits/{uuid.uuid4()}/extract").status_code == 404


# ── confirm — 닫힌 목록·편집 가능한 병원 정보 ──────────────────────────


def test_confirm_rejects_code_outside_list(app_client, svc_storage, monkeypatch):
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm",
        json=_confirm_json_body(reason_code="tumor"),
    )
    assert r.status_code == 422


def test_confirm_rejects_malformed_phone(app_client, svc_storage, monkeypatch):
    """카드번호 네 묶음 같은 모양은 `tel:` 링크가 되기 전에 여기서 막는다."""
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm",
        json=_confirm_json_body(hospital_phone="5432-1234-5678-9012"),
    )
    assert r.status_code == 422


def test_confirm_accepts_edited_hospital_fields(app_client, svc_storage, monkeypatch):
    """확인 화면에서 고친 병원 이름·주소·전화가 그대로 저장된다."""
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm",
        json=_confirm_json_body(
            hospital_name="고친병원",
            hospital_address="서울시 강남구",
            hospital_phone="02-123-4567",
        ),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hospital_name"] == "고친병원"
    assert body["hospital_address"] == "서울시 강남구"
    assert body["hospital_phone"] == "02-123-4567"
    assert body["suggested_reason_code"] == "skin"
    assert body["reason_code"] == "cardiac"


def test_confirm_request_schema_cannot_carry_items():
    """요청 본문의 items 를 믿으면 앱이 동의 분기를 우회한다 — 그런 필드가 없어야 한다."""
    assert "items" not in VetVisitConfirmRequest.model_fields
    assert "raw_ocr_items" not in VetVisitConfirmRequest.model_fields


def test_confirm_ignores_unknown_items_field_in_body(app_client, svc_storage, monkeypatch):
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm",
        json={**_confirm_json_body(), "items": [{"name": "주입", "amount_krw": 1}]},
    )
    assert r.status_code == 200, r.text


def test_confirm_of_other_users_draft_is_404(
    app_client, app_client_other, svc_storage, monkeypatch
):
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    r = app_client_other.post(
        f"/app/vet-visits/{started['draft_id']}/confirm", json=_confirm_json_body()
    )
    assert r.status_code == 404


# ── 목록·삭제 ────────────────────────────────────────────────────────


def test_list_visits_returns_confirmed_records(app_client, svc_storage, monkeypatch):
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    app_client.post(f"/app/vet-visits/{started['draft_id']}/confirm", json=_confirm_json_body())
    r = app_client.get("/app/vet-visits", params={"pet_id": str(SVC_PET)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["visits"]) == 1
    assert body["visits"][0]["total_krw"] == 80000


class _FrozenClock:
    """UTC 로는 9/12 23:30, **KST 로는 9/13 08:30** — 목록이 하루를 통째로 놓치던 그
    9시간 안이다. `now(tz)` 만 있으면 되는 자리라 `datetime` 을 통째로 대역한다."""

    _AT = datetime(2026, 9, 12, 23, 30, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls._AT if tz is None else cls._AT.astimezone(tz)


def test_today_is_kst_not_utc(monkeypatch):
    """`datetime.now(UTC).date()` 였을 때 이 자리가 9/12 를 냈다."""
    monkeypatch.setattr(vet_service, "datetime", _FrozenClock)
    assert vet_service.today_kst() == date(2026, 9, 13)
    assert vet_service._window(None, None)[1] == date(2026, 9, 13)


def test_list_shows_a_receipt_dated_today_kst(app_client, svc_storage, monkeypatch):
    """테스터 제보의 회귀 — 오늘(KST) 영수증을 아침에 확정하면 목록에서 사라졌다.

    UTC 의 오늘이 아직 9/12 라 목록의 `end` 가 어제였고, `visited_on=9/13` 이 창 밖으로
    떨어졌다. 확정은 200 이고 재인식은 `possible_duplicate` 를 냈다(그쪽은 창이 없다) —
    "저장은 됐다는데 저장소에 없다" 가 그 조합이다.
    """
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    monkeypatch.setattr(vet_service, "datetime", _FrozenClock)
    confirmed = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm",
        json=_confirm_json_body(visited_on="2026-09-13"),
    )
    assert confirmed.status_code == 200, confirmed.text

    body = app_client.get("/app/vet-visits", params={"pet_id": str(SVC_PET)}).json()
    assert body["end"] == "2026-09-13"
    assert [v["visited_on"] for v in body["visits"]] == ["2026-09-13"]


def test_confirm_rejects_a_future_date(app_client, svc_storage, monkeypatch):
    """미래로 확정된 기록은 어떤 창으로도 안 잡힌다(`end` 는 늘 오늘) — 저장 뒤에
    못 고치느니 확인 화면에서 되돌린다."""
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    monkeypatch.setattr(vet_service, "datetime", _FrozenClock)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm",
        json=_confirm_json_body(visited_on="2026-09-20"),
    )
    assert r.status_code == 422


def test_confirm_allows_one_day_ahead_for_timezones(app_client, svc_storage, monkeypatch):
    """여유가 0 이 아닌 이유 — KST 보다 앞선 시간대(최대 UTC+14)의 오늘은 KST 로 내일이다."""
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    monkeypatch.setattr(vet_service, "datetime", _FrozenClock)
    r = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm",
        json=_confirm_json_body(visited_on="2026-09-14"),
    )
    assert r.status_code == 200, r.text


def test_list_counts_records_older_than_the_window(app_client, svc_store, svc_storage, monkeypatch):
    """묵은 영수증은 기본 창(최근 1년) 밖이라 안 보인다 — 그 사실을 앱이 말할 수 있게
    건수를 같이 준다. 0 이면 안 띄우면 되니 평소 화면은 안 빡빡해진다."""
    svc_store.visits[uuid.uuid4()] = VetVisit(
        app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2019, 5, 17), total_krw=61700,
        reason_code="skin", client_event_id=uuid.uuid4(),
    )
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    app_client.post(f"/app/vet-visits/{started['draft_id']}/confirm", json=_confirm_json_body())

    body = app_client.get("/app/vet-visits", params={"pet_id": str(SVC_PET)}).json()
    assert len(body["visits"]) == 1  # 창 안의 것만
    assert body["older_count"] == 1  # 창 밖에 하나 더 있다


def test_list_reaches_an_old_record_with_an_explicit_window(app_client, svc_store):
    """날짜를 고르면 그 옛날도 보인다 — 기본 창이 감추는 것이지 잃어버리는 것이 아니다."""
    # 응답으로 직렬화되는 유일한 행이라 DB 기본값이 채워 주는 칸까지 손으로 세운다.
    visit_id = uuid.uuid4()
    svc_store.visits[visit_id] = VetVisit(
        id=visit_id, app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2019, 5, 17),
        total_krw=61700, reason_code="skin", client_event_id=uuid.uuid4(),
        is_emergency=False, is_oncology=False, created_at=datetime.now(UTC),
    )
    r = app_client.get(
        "/app/vet-visits",
        params={"pet_id": str(SVC_PET), "from": "2019-01-01", "to": "2019-12-31"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [v["visited_on"] for v in body["visits"]] == ["2019-05-17"]
    assert body["older_count"] == 0


def test_list_has_no_range_cap(app_client, svc_store):
    """저장소의 [전체] — `from` 을 아무리 멀리 잡아도 422 가 아니다. 상한이 있으면
    앱이 창을 쪼개 여러 번 불러야 하고, 쪼개는 코드는 경계에서 한 건씩 흘린다."""
    visit_id = uuid.uuid4()
    svc_store.visits[visit_id] = VetVisit(
        id=visit_id, app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2013, 4, 1),
        total_krw=30000, reason_code="vaccination", client_event_id=uuid.uuid4(),
        is_emergency=False, is_oncology=False, created_at=datetime.now(UTC),
    )
    r = app_client.get(
        "/app/vet-visits", params={"pet_id": str(SVC_PET), "from": "0001-01-01"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [v["visited_on"] for v in body["visits"]] == ["2013-04-01"]
    assert body["older_count"] == 0


def test_list_visits_rejects_reversed_range(app_client):
    r = app_client.get(
        "/app/vet-visits",
        params={"pet_id": str(SVC_PET), "from": "2026-09-02", "to": "2026-09-01"},
    )
    assert r.status_code == 422


def test_list_visits_rejects_pet_i_do_not_own(app_client):
    r = app_client.get("/app/vet-visits", params={"pet_id": str(uuid.uuid4())})
    assert r.status_code == 404


def test_delete_visit_removes_it(app_client, svc_store, svc_storage, monkeypatch):
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    confirmed = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm", json=_confirm_json_body()
    ).json()
    r = app_client.delete(f"/app/vet-visits/{confirmed['id']}")
    assert r.status_code == 204
    assert svc_store.visits == {}


def test_delete_of_other_users_visit_is_404(app_client, app_client_other, svc_storage, monkeypatch):
    started, _extracted = _extract(app_client, svc_storage, monkeypatch)
    confirmed = app_client.post(
        f"/app/vet-visits/{started['draft_id']}/confirm", json=_confirm_json_body()
    ).json()
    r = app_client_other.delete(f"/app/vet-visits/{confirmed['id']}")
    assert r.status_code == 404


# ── reason-options 는 `/{draft_id}` 보다 먼저 선언돼야 한다 ────────────


def test_reason_options_endpoint_is_not_shadowed_by_draft_id_route(app_client):
    """`/reason-options` 가 `/{draft_id}/...` 뒤에 있으면 이 요청이 그쪽으로 샌다."""
    r = app_client.get("/app/vet-visits/reason-options", params={"pet_id": str(SVC_PET)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {o["code"] for o in body} == set(VET_REASON_CODES)
    assert all(o["label"] == VET_REASON_LABELS[o["code"]] for o in body)


# ── bridge — local 저장소의 업로드/다운로드 (fix round 1) ──────────────
#
# 티켓의 `upload_url`/스토리지 키가 실제로 뭔가를 가리켜야 사진이 backend 에
# 도착한다. `screening.py` 의 bridge 테스트와 같은 결 — 인증 헤더 없이, 키 자체가
# 자격이다.


def _upload_bridge(app_client: TestClient, url: str, data: bytes, content_type="image/jpeg"):
    return app_client.put(
        url.removeprefix("http://x"), content=data, headers={"Content-Type": content_type}
    )


def test_bridge_upload_then_download_round_trips_the_same_bytes(app_client, svc_storage):
    started = app_client.post("/app/vet-visits", json=_start_body()).json()
    r = _upload_bridge(app_client, started["upload_url"], b"receipt-bytes")
    assert r.status_code == 200, r.text

    got = app_client.get(f"/app/vet-visits/_bridge/download/{started['storage_key']}")
    assert got.status_code == 200, got.text
    assert got.content == b"receipt-bytes"


def test_bridge_upload_rejects_oversized_body(app_client, svc_storage, monkeypatch):
    monkeypatch.setattr(vet_service, "MAX_RECEIPT_BYTES", 4)
    started = app_client.post("/app/vet-visits", json=_start_body()).json()
    r = _upload_bridge(app_client, started["upload_url"], b"x" * 40)
    assert r.status_code == 413
    assert not svc_storage.local_path(started["storage_key"]).exists()


def test_bridge_upload_rejects_mismatched_content_type(app_client, svc_storage):
    """티켓은 `image/jpeg` 로 발급됐는데 다른 형식으로 밀어 넣는 경우."""
    started = app_client.post("/app/vet-visits", json=_start_body(content_type="image/jpeg")).json()
    r = _upload_bridge(app_client, started["upload_url"], b"webp-bytes", content_type="image/webp")
    assert r.status_code == 415
    assert not svc_storage.local_path(started["storage_key"]).exists()


def test_bridge_upload_rejects_unknown_key(app_client):
    """backend 가 발급하지 않은 키 — 존재하는 척도 안 한다."""
    r = _upload_bridge(app_client, "/app/vet-visits/_bridge/upload/made-up.jpg", b"x")
    assert r.status_code == 404


def test_bridge_download_of_unknown_key_is_404(app_client):
    r = app_client.get("/app/vet-visits/_bridge/download/made-up.jpg")
    assert r.status_code == 404
