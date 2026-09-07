"""감사 로그 조회 — services/audit.py 의 읽기 절과 routers/admin_audit.py (A4-1 · #221).

`test_admin_audit_log.py` 가 **쓰는 쪽**(무엇이 언제 확정되는가)을 보고, 여기는 **읽는
쪽**을 봅니다.

**여기서 지키려는 것 넷:**

  ① ADMIN 만 본다 — OPERATOR 는 복호화는 해도 누가 했는지는 못 본다 (의도한 비대칭)
  ② 주체 없는 행이 안 사라진다 — INNER JOIN 으로 바꾸면 제일 보고 싶은 것부터 없어진다
  ③ 페이지가 겹치지도 빠지지도 않는다 — 읽는 동안에도 행이 늘기 때문
  ④ 이 조회는 감사에 안 남는다 — 남기면 화면이 자기 기록으로 채워진다

`Store` 는 커밋 경계만 흉내 내므로 목록 조회는 **실제 SQL 을 안 탑니다.** 정렬·키셋
비교가 진짜로 맞는지는 `uv run dev` 로 실제 DB 에 붙여 봐야 합니다 — 그래서 여기서는
**서비스의 판단**(커서 왕복 · limit+1 · 다음 쪽 유무)과 **라우터의 경계**만 봅니다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAdmin, FakeSession, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Principal, current_admin
from daengs_backend.routers import admin_audit as router_module
from daengs_backend.services import audit as service


class FakeEntry:
    """`AdminAuditLog` 대역. 서비스가 건드리는 속성만 있습니다."""

    def __init__(
        self,
        *,
        action: str,
        created_at: datetime,
        admin_user_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        detail: dict | None = None,
        ip: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.action = action
        self.created_at = created_at
        self.admin_user_id = admin_user_id
        self.target_type = target_type
        self.target_id = target_id
        self.detail = detail
        self.ip = ip
        self.request_id = request_id


BASE = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    return install(Store(FakeAdmin()), monkeypatch)


@pytest.fixture
def rows(store: Store, monkeypatch: pytest.MonkeyPatch) -> list[FakeEntry]:
    """최근 순으로 5개. 하나는 **주체가 없습니다** (없는 아이디 로그인 실패)."""
    entries = [
        FakeEntry(
            action="admin.login.success",
            created_at=BASE - timedelta(minutes=i),
            admin_user_id=store.admin.id,
        )
        for i in range(4)
    ]
    entries.append(
        FakeEntry(
            action="admin.login.failed_unknown_id",
            created_at=BASE - timedelta(minutes=4),
            admin_user_id=None,
            detail={"login_id": "nobody"},
        )
    )

    from daengs_backend.repositories import admin_audit_log as audit_repo

    async def fake_list(session, *, limit, before=None, action_prefix=None, **_kw):
        kept = entries
        if action_prefix is not None:
            kept = [e for e in kept if e.action.startswith(action_prefix)]
        if before is not None:
            at, last_id = before
            kept = [e for e in kept if (e.created_at, e.id) < (at, last_id)]
        # 진짜 쿼리와 같은 모양: (행, login_id, name)
        return [
            (
                e,
                store.admin.login_id if e.admin_user_id else None,
                store.admin.name if e.admin_user_id else None,
            )
            for e in kept[:limit]
        ]

    monkeypatch.setattr(audit_repo, "list_entries", fake_list)
    return entries


@pytest.fixture
def session(store: Store) -> FakeSession:
    return FakeSession(store)


class TestCursor:
    def test_왕복한다(self) -> None:
        at, entry_id = BASE, uuid.uuid4()
        assert service._decode_cursor(service._encode_cursor(at, entry_id)) == (
            at,
            entry_id,
        )

    @pytest.mark.parametrize("bad", ["", "!!!", "bm90LWEtY3Vyc29y", "YQ=="])
    def test_손댄_값은_예외지_500_이_아니다(self, bad: str) -> None:
        """커서는 URL 에 그대로 실려 오므로 사람이 고친 값이 들어옵니다."""
        with pytest.raises(service.InvalidCursorError):
            service._decode_cursor(bad)


class TestListEntries:
    async def test_한_쪽과_다음_커서(self, session, rows) -> None:
        page = await service.list_entries(session, limit=2)  # type: ignore[arg-type]

        assert len(page.entries) == 2
        assert page.next_cursor is not None

    async def test_마지막_쪽은_커서가_없다(self, session, rows) -> None:
        """`limit + 1` 을 읽어 다음 쪽 유무를 봅니다 — 안 그러면 화면이 빈 쪽을
        한 번 더 부릅니다."""
        page = await service.list_entries(session, limit=50)  # type: ignore[arg-type]

        assert len(page.entries) == len(rows)
        assert page.next_cursor is None

    async def test_쪽이_겹치지도_빠지지도_않는다(self, session, rows) -> None:
        """이 테이블은 **읽는 동안에도 늡니다** — OFFSET 이면 여기서 어긋납니다."""
        seen: list[uuid.UUID] = []
        cursor = None
        for _ in range(10):
            page = await service.list_entries(session, limit=2, cursor=cursor)  # type: ignore[arg-type]
            seen.extend(e.entry.id for e in page.entries)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert len(seen) == len(set(seen)), "같은 행이 두 번 나왔습니다"
        assert set(seen) == {e.id for e in rows}, "빠진 행이 있습니다"

    async def test_주체_없는_행이_살아_있다(self, session, rows) -> None:
        """INNER JOIN 으로 바꾸면 **이 행부터** 사라집니다."""
        page = await service.list_entries(session, limit=50)  # type: ignore[arg-type]

        anonymous = [e for e in page.entries if e.entry.admin_user_id is None]
        assert len(anonymous) == 1
        assert anonymous[0].actor_login_id is None
        assert anonymous[0].entry.detail == {"login_id": "nobody"}

    async def test_접두어로_갈래를_고른다(self, session, rows) -> None:
        page = await service.list_entries(  # type: ignore[arg-type]
            session, limit=50, action_prefix="admin.login.failed"
        )
        assert {e.entry.action for e in page.entries} == {
            "admin.login.failed_unknown_id"
        }

    async def test_limit_은_MAX_로_잘린다(self, session, rows) -> None:
        page = await service.list_entries(session, limit=99999)  # type: ignore[arg-type]
        assert len(page.entries) <= service.MAX_LIMIT

    async def test_조회는_감사에_안_남는다(self, session, store, rows) -> None:
        """남기면 이 화면이 자기 기록으로 채워지고, 그 행을 본 것도 남겨야 합니다."""
        await service.list_entries(session, limit=50)  # type: ignore[arg-type]

        assert store.audit_log == []
        assert store.audit_pending == []


class TestHttpBoundary:
    @pytest.fixture
    def app(self, store: Store) -> FastAPI:
        test_app = FastAPI()
        test_app.include_router(router_module.router)

        async def _fake_session() -> FakeSession:
            return FakeSession(store)

        test_app.dependency_overrides[get_session] = _fake_session
        return test_app

    @pytest.fixture
    def as_role(self, app: FastAPI, store: Store):
        def _make(role: str) -> TestClient:
            principal = Principal(admin_id=store.admin.id, role=role)

            async def _fake_admin() -> Principal:
                return principal

            app.dependency_overrides[current_admin] = _fake_admin
            return TestClient(app)

        return _make

    def test_ADMIN_만_본다(self, as_role, rows) -> None:
        """**이 카드의 진짜 산출물입니다.**

        `OPERATOR` 는 `pii:read` 를 가져 원문을 열 수 있지만, 누가 열었는지는 못 봅니다.
        비대칭이지만 의도한 것입니다 — 감사의 값은 "본 사람이 나중에 따져질 수 있다"
        에서 나오고, 보는 쪽과 따지는 쪽이 같으면 그 값이 줄어듭니다.
        """
        assert as_role("ADMIN").get("/admin/audit").status_code == 200
        for role in ("OPERATOR", "CURATOR", "ANALYST", "VIEWER"):
            assert as_role(role).get("/admin/audit").status_code == 403, role

    def test_주체_없는_행이_actor_null_로_나간다(self, as_role, rows) -> None:
        body = as_role("ADMIN").get("/admin/audit").json()

        anonymous = [e for e in body["entries"] if e["actor"] is None]
        assert len(anonymous) == 1
        # 무엇을 시도했는지는 detail 에만 있습니다. 화면이 그것을 읽습니다.
        assert anonymous[0]["detail"] == {"login_id": "nobody"}

    def test_주체_있는_행은_이름이_같이_나간다(self, as_role, store, rows) -> None:
        """UUID 만 주면 화면이 못 읽습니다."""
        body = as_role("ADMIN").get("/admin/audit").json()

        named = [e for e in body["entries"] if e["actor"] is not None]
        assert named
        assert named[0]["actor"]["login_id"] == store.admin.login_id
        assert named[0]["actor"]["name"] == store.admin.name

    def test_총_개수를_안_준다(self, as_role, rows) -> None:
        """읽는 사이에도 늘어서 곧 틀린 숫자가 됩니다."""
        body = as_role("ADMIN").get("/admin/audit").json()
        assert set(body) == {"entries", "next_cursor"}

    def test_손댄_커서는_422(self, as_role, rows) -> None:
        res = as_role("ADMIN").get("/admin/audit", params={"cursor": "!!!"})
        assert res.status_code == 422

    def test_limit_범위를_넘으면_422(self, as_role, rows) -> None:
        assert as_role("ADMIN").get(
            "/admin/audit", params={"limit": 0}
        ).status_code == 422
        assert as_role("ADMIN").get(
            "/admin/audit", params={"limit": service.MAX_LIMIT + 1}
        ).status_code == 422


@pytest.fixture
def counts(monkeypatch: pytest.MonkeyPatch):
    """`retention_summary` 리포지토리를 갈아 끼웁니다 — 실제 SQL 은 안 탑니다.

    `rows` 와 같은 이유로 리포지토리 자리에서 끊습니다. `Store` 는 커밋 경계만 흉내 내므로
    `count(*)` 가 진짜로 맞는지는 여기서 못 봅니다. 여기서 보는 것은 **판단**입니다.
    """
    from daengs_backend.repositories import admin_audit_log as audit_repo

    def _install(total: int, *, empty: bool = False) -> None:
        async def fake_summary(_session):  # type: ignore[no-untyped-def]
            if empty:
                return (0, None, None)
            return (
                total,
                datetime(2026, 9, 4, tzinfo=UTC),
                datetime(2026, 9, 7, tzinfo=UTC),
            )

        monkeypatch.setattr(audit_repo, "retention_summary", fake_summary)

    return _install


class TestRetention:
    """보존 요약 — A5 가 "지우지 않는다" 로 닫히면서 생긴 자리 (#297).

    **A5 의 결정은 "지우는 주기를 두지 않는다" 입니다** (2026-09-07). 그 결정이 "안 본다"로
    무너지지 않게 기준을 숫자로 박았고, 이 코드가 그 기준을 들고 있습니다. 그래서 여기서
    지키는 것은 **기준이 한 곳에만 사는가**입니다.
    """

    async def test_기준_아래면_over_가_아니다(self, session, counts) -> None:
        counts(56)
        summary = await service.retention_summary(session)  # type: ignore[arg-type]

        assert summary.total == 56
        assert summary.threshold == service.RETENTION_ROW_THRESHOLD
        assert summary.over_threshold is False

    async def test_기준에_닿으면_over_다(self, session, counts) -> None:
        """**경계는 `>=` 입니다.** 딱 기준일 때 안 걸리면 아무도 못 알아챕니다."""
        counts(service.RETENTION_ROW_THRESHOLD)
        summary = await service.retention_summary(session)  # type: ignore[arg-type]

        assert summary.over_threshold is True

    async def test_비어_있어도_안_죽는다(self, session, counts) -> None:
        """행이 하나도 없으면 min/max 가 NULL 입니다 — 새 환경의 정상 상태입니다."""
        counts(0, empty=True)
        summary = await service.retention_summary(session)  # type: ignore[arg-type]

        assert summary.total == 0
        assert summary.oldest_at is None
        assert summary.over_threshold is False

    async def test_조회는_감사에_안_남는다(self, session, store, counts) -> None:
        """목록과 같은 이유입니다 — 남기면 화면이 자기 기록으로 채워집니다."""
        counts(56)
        await service.retention_summary(session)  # type: ignore[arg-type]

        assert store.audit_log == []
        assert store.audit_pending == []


class TestRetentionHttpBoundary:
    """권한은 목록과 같은 `admin:manage` 입니다 — 새 `Perm` 을 만들지 않았습니다."""

    @pytest.fixture
    def app(self, store: Store) -> FastAPI:
        test_app = FastAPI()
        test_app.include_router(router_module.router)

        async def _fake_session() -> FakeSession:
            return FakeSession(store)

        test_app.dependency_overrides[get_session] = _fake_session
        return test_app

    @pytest.fixture
    def as_role(self, app: FastAPI, store: Store):
        def _make(role: str) -> TestClient:
            principal = Principal(admin_id=store.admin.id, role=role)

            async def _fake_admin() -> Principal:
                return principal

            app.dependency_overrides[current_admin] = _fake_admin
            return TestClient(app)

        return _make

    def test_ADMIN_만_본다(self, as_role, counts) -> None:
        counts(56)
        assert as_role("ADMIN").get("/admin/audit/retention").status_code == 200
        for role in ("OPERATOR", "CURATOR", "ANALYST", "VIEWER"):
            assert as_role(role).get("/admin/audit/retention").status_code == 403, role

    def test_기준을_같이_내려_준다(self, as_role, counts) -> None:
        """**화면이 기준을 직접 들고 있지 않게** 응답에 같이 실어 보냅니다.

        `total >= 100000` 을 화면이 쓰면 기준이 두 곳에 살고, 나중에 바꿀 때 한쪽만 바뀝니다.
        """
        counts(56)
        body = as_role("ADMIN").get("/admin/audit/retention").json()

        assert body["total"] == 56
        assert body["threshold"] == service.RETENTION_ROW_THRESHOLD
        assert body["over_threshold"] is False
        assert body["oldest_at"].startswith("2026-09-04")

    def test_목록_경로와_안_겹친다(self, as_role, counts, rows) -> None:
        """`/admin/audit` 와 `/admin/audit/retention` 이 서로를 잡아먹지 않습니다."""
        counts(56)
        client = as_role("ADMIN")

        assert set(client.get("/admin/audit").json()) == {"entries", "next_cursor"}
        assert "total" in client.get("/admin/audit/retention").json()
