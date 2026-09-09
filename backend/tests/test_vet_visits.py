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

from daengs_backend.core.storage import LocalBridgeStorage
from daengs_backend.models import VET_REASON_CODES, VetVisit, VetVisitDraft
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import vet_visit as vet_repo
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

    async def get_by_client_event(_session, app_user_id, client_event_id):
        return next(
            (
                v
                for v in visits.values()
                if v.app_user_id == app_user_id and v.client_event_id == client_event_id
            ),
            None,
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
    monkeypatch.setattr(vet_repo, "get_draft_by_sha", get_draft_by_sha)
    monkeypatch.setattr(vet_repo, "expired_drafts", expired_drafts)
    monkeypatch.setattr(vet_repo, "find_duplicate", find_duplicate)
    monkeypatch.setattr(vet_repo, "get_by_client_event", get_by_client_event)
    monkeypatch.setattr(vet_repo, "list_between", list_between)
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

    visit = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft2.id, _confirm_body())
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


# ── confirm — items 는 초안에서만 ───────────────────────────────────────


def _confirm_body(**kw) -> "vet_service.ConfirmDraftRequest":
    defaults = {
        "client_event_id": uuid.uuid4(),
        "reason_code": "cardiac",
        "visited_on": date(2026, 9, 2),
        "total_krw": 80000,
    }
    defaults.update(kw)
    return vet_service.ConfirmDraftRequest(**defaults)


async def test_confirm_reads_items_from_draft_not_request(
    svc_session, svc_store, svc_storage, monkeypatch
):
    """요청 본문의 items 를 믿으면 앱이 동의 분기를 우회한다."""
    _consent(svc_store, at=datetime.now(UTC), version="v1")
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)
    stored_items = draft.extracted["items"]

    body = _confirm_body(items=[{"name": "주입", "amount_krw": 1}])
    visit = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert visit.raw_ocr_items == stored_items
    assert visit.raw_ocr_items != body.items


async def test_confirm_without_consent_stores_empty_items(
    svc_session, svc_store, svc_storage, monkeypatch
):
    _consent(svc_store, at=None, version=None)
    draft, _t, _c = await _start(svc_session, svc_store)
    _upload(svc_storage, draft)
    monkeypatch.setattr(vet_receipt, "extract", _counting_extract([]))
    await vet_service.extract_draft(svc_session, SVC_OWNER, draft.id)

    body = _confirm_body()
    visit = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
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
    visit = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
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

    visit = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, _confirm_body())
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
    first = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert draft.id not in svc_store.drafts
    # 두 번째는 draft 가 이미 지워졌어도 vet_visits 자신의 멱등키로 잡힌다 — 404 가 아니다.
    second = await vet_service.confirm_draft(svc_session, SVC_OWNER, draft.id, body)
    assert second is first
    assert len(svc_store.visits) == 1


# ── reason_options ───────────────────────────────────────────────────


async def test_reason_options_puts_recent_first_then_the_rest(svc_session, svc_store):
    svc_store.visits[uuid.uuid4()] = VetVisit(
        app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2026, 8, 1), total_krw=1000,
        reason_code="skin", client_event_id=uuid.uuid4(),
    )
    svc_store.visits[uuid.uuid4()] = VetVisit(
        app_user_id=SVC_OWNER, pet_id=SVC_PET, visited_on=date(2026, 9, 1), total_krw=1000,
        reason_code="cardiac", client_event_id=uuid.uuid4(),
    )
    options = await vet_service.reason_options(svc_session, SVC_OWNER, SVC_PET)
    assert options[0] == "cardiac"  # 가장 최근
    assert options[1] == "skin"
    assert set(options) == set(VET_REASON_CODES)
    assert len(options) == len(VET_REASON_CODES)


async def test_reason_options_rejects_pet_i_do_not_own(svc_session, svc_store):
    with pytest.raises(vet_service.VetVisitNotFoundError):
        await vet_service.reason_options(svc_session, SVC_OWNER, uuid.uuid4())
