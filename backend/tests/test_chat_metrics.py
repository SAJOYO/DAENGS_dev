"""운영 지표 집계 — services/metrics.py 와 routers/metrics.py (B3 · #223).

**이 카드는 실물로 숫자를 맞춰 볼 수가 없습니다.** 2026-09-04 기준 개발 DB 의
`chat_turns` 가 **0건**입니다 (#131 이 스키마를 넣었지만 앱이 아직 대화를 안 쌓았습니다).
그래서 집계식이 맞는지는 여기서만 확인됩니다 — 화면에서 볼 수 있는 것은 "0건일 때
안 죽는가" 뿐입니다.

**여기서 지키려는 것 넷:**

  ① 0건에서 안 죽는다 — 지금 유일하게 실제로 밟히는 경로다
  ② 원문이 응답에 없다 — D-037. 스키마에 칸이 없어야 실수로도 안 나간다
  ③ `processing_status='failed'` 와 `assistant_status='FAILED'` 가 안 섞인다
  ④ 능력 분포의 분모가 turn 수가 아니다 — 배열이라 합이 turn 수를 넘는다

리포지토리는 실제 SQL 이라 여기서 안 탑니다. **집계 조합과 경계만** 봅니다.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAdmin, FakeSession, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Principal, current_admin
from daengs_backend.routers import metrics as router_module
from daengs_backend.services import metrics as service


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    return install(Store(FakeAdmin()), monkeypatch)


def _install_repo(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sessions: int = 0,
    processing: dict[str, int] | None = None,
    assistant: dict[str, int] | None = None,
    errors: list[tuple[str, int]] | None = None,
    categories: list[tuple[str, int]] | None = None,
    summaries: dict[str, int] | None = None,
) -> dict[str, datetime | None]:
    """리포지토리 여섯을 갈아 끼웁니다. 넘어온 `since` 도 잡아 둡니다."""
    from daengs_backend.repositories import chat_metrics as repo

    seen: dict[str, datetime | None] = {"since": None}

    async def _sessions(_s, *, since):
        seen["since"] = since
        return sessions

    async def _processing(_s, *, since):
        return dict(processing or {})

    async def _assistant(_s, *, since):
        return dict(assistant or {})

    async def _errors(_s, *, since):
        return list(errors or [])

    async def _categories(_s, *, since):
        return list(categories or [])

    async def _summaries(_s, *, since):
        return dict(summaries or {})

    monkeypatch.setattr(repo, "count_sessions", _sessions)
    monkeypatch.setattr(repo, "count_turns_by_processing_status", _processing)
    monkeypatch.setattr(repo, "count_turns_by_assistant_status", _assistant)
    monkeypatch.setattr(repo, "count_error_codes", _errors)
    monkeypatch.setattr(repo, "count_agent_categories", _categories)
    monkeypatch.setattr(repo, "count_summaries_by_processing_status", _summaries)
    return seen


class TestCollect:
    async def test_0건에서_안_죽는다(self, store, monkeypatch) -> None:
        """**지금 개발 DB 가 정확히 이 상태입니다.** 유일하게 실제로 밟히는 경로입니다."""
        _install_repo(monkeypatch)

        m = await service.collect_chat_metrics(FakeSession(store))  # type: ignore[arg-type]

        assert m.sessions == 0
        assert m.turns_total == 0
        assert m.tagged_total == 0
        assert m.categories == []

    async def test_turn_총수는_따로_세지_않는다(self, store, monkeypatch) -> None:
        """따로 세면 두 쿼리 사이에 행이 늘어 **합이 안 맞는 화면**이 나옵니다."""
        _install_repo(
            monkeypatch, processing={"completed": 7, "failed": 2, "processing": 1}
        )

        m = await service.collect_chat_metrics(FakeSession(store))  # type: ignore[arg-type]

        assert m.turns_total == 10
        assert sum(m.turns_by_processing_status.values()) == m.turns_total

    async def test_능력_분포의_합이_turn_수가_아니다(self, store, monkeypatch) -> None:
        """`agent_categories` 가 배열이라 turn 하나가 여러 태그를 답니다.

        화면이 turn 수를 분모로 쓰면 100%를 넘습니다 — 그래서 분모를 따로 냅니다.
        """
        _install_repo(
            monkeypatch,
            processing={"completed": 3},
            categories=[("life", 3), ("training", 2), ("place", 1)],
        )

        m = await service.collect_chat_metrics(FakeSession(store))  # type: ignore[arg-type]

        assert m.turns_total == 3
        assert m.tagged_total == 6, "태그 합이 turn 수와 다른 것이 정상입니다"

    @pytest.mark.parametrize(
        ("given", "expected"),
        [(0, 1), (-5, 1), (1, 1), (30, 30), (service.MAX_DAYS + 100, service.MAX_DAYS)],
    )
    async def test_기간이_잘린다(self, store, monkeypatch, given, expected) -> None:
        seen = _install_repo(monkeypatch)

        m = await service.collect_chat_metrics(FakeSession(store), days=given)  # type: ignore[arg-type]

        assert m.days == expected
        # `since` 가 실제로 그 기간만큼 과거여야 합니다.
        assert seen["since"] is not None
        delta = datetime.now(UTC) - seen["since"]
        assert abs(delta - timedelta(days=expected)) < timedelta(seconds=5)


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

    def test_VIEWER_만_막힌다(self, as_role, monkeypatch) -> None:
        """`ANALYST` 라는 role 이 존재하는 이유가 이 화면입니다 (`core/deps.py`)."""
        _install_repo(monkeypatch)

        for role in ("ADMIN", "OPERATOR", "CURATOR", "ANALYST"):
            assert as_role(role).get("/admin/metrics/chats").status_code == 200, role
        assert as_role("VIEWER").get("/admin/metrics/chats").status_code == 403

    def test_원문이_응답에_없다(self, as_role, monkeypatch) -> None:
        """D-037. 스키마에 칸 자체가 없어서 실수로도 안 나갑니다."""
        _install_repo(
            monkeypatch,
            processing={"completed": 1},
            assistant={"ANSWERED": 1},
            categories=[("life", 1)],
        )

        body = as_role("ADMIN").get("/admin/metrics/chats").text

        for leaked in (
            "user_content",
            "assistant_content",
            "public_response",
            "title",
            "question_summary",
        ):
            assert leaked not in body

    def test_처리_실패와_응답_FAILED_가_안_섞인다(self, as_role, monkeypatch) -> None:
        """`07_chats.sql` 의 state CHECK 상 **같은 행에 못 옵니다.**

        합치면 "우리 코드가 죽은 것" 과 "어시스턴트가 못 답한 것" 이 섞이는데,
        고칠 사람이 다른 두 숫자입니다.
        """
        _install_repo(
            monkeypatch,
            processing={"completed": 8, "failed": 2},
            assistant={"ANSWERED": 6, "FAILED": 2},
            errors=[("provider_timeout", 2)],
        )

        body = as_role("ADMIN").get("/admin/metrics/chats").json()["turns"]

        by_proc = {c["name"]: c["count"] for c in body["by_processing_status"]}
        by_asst = {c["name"]: c["count"] for c in body["by_assistant_status"]}
        assert by_proc["failed"] == 2
        assert by_asst["FAILED"] == 2
        # 총 10건 중 처리 실패 2 + 응답 FAILED 2 는 **서로 다른 4건**입니다.
        assert body["total"] == 10
        assert body["top_error_codes"] == [{"name": "provider_timeout", "count": 2}]

    def test_분포가_많은_순이고_동점은_이름_순(self, as_role, monkeypatch) -> None:
        """새로고침해도 순서가 안 흔들려야 합니다."""
        _install_repo(monkeypatch, assistant={"REFUSED": 3, "ANSWERED": 3, "PARTIAL": 5})

        names = [
            c["name"]
            for c in as_role("ADMIN").get("/admin/metrics/chats").json()["turns"][
                "by_assistant_status"
            ]
        ]
        assert names == ["PARTIAL", "ANSWERED", "REFUSED"]

    def test_기간을_응답에_실어_보낸다(self, as_role, monkeypatch) -> None:
        """화면이 "왜 이 숫자냐" 를 말할 수 있어야 합니다."""
        _install_repo(monkeypatch)

        body = as_role("ADMIN").get("/admin/metrics/chats", params={"days": 7}).json()

        assert body["days"] == 7
        assert body["since"]

    def test_기간_범위를_넘으면_422(self, as_role, monkeypatch) -> None:
        _install_repo(monkeypatch)

        assert as_role("ADMIN").get(
            "/admin/metrics/chats", params={"days": 0}
        ).status_code == 422
        assert as_role("ADMIN").get(
            "/admin/metrics/chats", params={"days": service.MAX_DAYS + 1}
        ).status_code == 422

    def test_0건_응답도_모양이_온전하다(self, as_role, monkeypatch) -> None:
        """화면이 빈 배열에서 죽지 않게, 칸은 다 있고 값만 비어야 합니다."""
        _install_repo(monkeypatch)

        body = as_role("ADMIN").get("/admin/metrics/chats").json()

        assert body["sessions"] == 0
        assert body["turns"]["total"] == 0
        assert body["turns"]["by_processing_status"] == []
        assert body["categories"] == {"counts": [], "tagged_total": 0}
        assert body["summaries"] == []
