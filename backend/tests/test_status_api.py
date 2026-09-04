"""상태 페이지 API — 문(門) · 저하 · 접점 (#180 · 콘솔 로드맵 B1).

**여기서 보는 것은 넷이다.**

1. 권한 — 조회는 `READ`. 읽기 전용이고 개인정보가 없다.
2. **항목 하나가 죽어도 200 이다.** 이 화면의 존재 이유가 그것이라, 카드의 작업 목록이
   테스트로 콕 집어 요구한 것도 이것이다. 500 을 주면 "서비스가 살아 있나"를 묻는 화면이
   그 질문 때문에 아무 말도 못 한다.
3. **`absent` 와 `down` 이 갈린다.** 로컬 서버와 GCP 는 같은 코드가 뜨는데 있는 것이 다르다
   (GCP 에 크롤러 없음 · gait 는 어디서도 profile 로 꺼짐). 환경 차이를 고장으로 칠하면
   화면이 늘 빨갛고, 빨간 게 늘 있으면 아무도 안 본다.
4. **Redis 예산 키 이름** — 접점을 안 늘리려고 `daengs_life.realtime.cache` 를 부르지 않고
   키를 직접 적었다(`services/status.py` 머리말). 그래서 이름이 두 군데가 됐고, 어긋나면
   카운터가 조용히 0 으로 보인다. 여기서 **저쪽 원본과 대조**해 그 어긋남을 잡는다.

DB 는 건드리지 않는다. 세션 의존성을 갈아끼운다 — 팀에 DB 가 하나뿐이라 API 테스트가
그것을 읽으러 가면 안 된다 (`test_crawl_api.py` 와 같은 규칙).
"""
from __future__ import annotations

import ast
import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.core.warm_up import STATE_ATTR, WarmUp, WarmUpPhase
from daengs_backend.schemas.status import StatusState
from daengs_backend.services import status as status_service

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """앱을 세우되 **바깥으로 나가는 것은 전부 막는다.**

    `with TestClient(app)` 를 쓰지 않는다 — lifespan 이 돌면 Redis 에 붙고 예열 스레드가
    뜬다. 예열 상태는 아래 `_warm_up` 이 직접 놓는다.
    """
    from daengs_backend.core.database import get_session
    from daengs_backend.main import app

    async def _ok_db(_session) -> tuple[StatusState, str]:
        return StatusState.OK, "테스트"

    async def _absent() -> tuple[StatusState, str]:
        return StatusState.ABSENT, "테스트"

    monkeypatch.setattr(status_service, "_db", _ok_db)
    monkeypatch.setattr(status_service, "_crawl", lambda _s: _absent())
    monkeypatch.setattr(status_service, "_screening", _absent)
    monkeypatch.setattr(status_service, "_redis", _absent)
    monkeypatch.setattr(status_service, "_place", _absent)
    monkeypatch.setattr(status_service, "_journey", _absent)
    monkeypatch.setattr(status_service, "_gait", _absent)

    app.dependency_overrides[get_session] = lambda: object()
    _warm_up(app, WarmUpPhase.READY)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _warm_up(app, phase: WarmUpPhase) -> None:
    setattr(app.state, STATE_ATTR, WarmUp(phase=phase))


def _auth(role: str = "ADMIN") -> dict[str, str]:
    token = create_access_token(uuid.uuid4(), SubjectType.ADMIN, role)
    return {"Authorization": f"Bearer {token}"}


def _items(client: TestClient, role: str = "ADMIN") -> dict[str, dict]:
    got = client.get("/admin/status", headers=_auth(role))
    assert got.status_code == 200, got.text
    return {item["name"]: item for item in got.json()["items"]}


# ---------------------------------------------------------------- 문
def test_토큰_없이_부르면_401(client: TestClient) -> None:
    assert client.get("/admin/status").status_code == 401


def test_읽기_권한만_있어도_본다(client: TestClient) -> None:
    """VIEWER 는 `READ` 만 가진다. 상태는 개인정보가 없어 그것으로 충분하다."""
    assert client.get("/admin/status", headers=_auth("VIEWER")).status_code == 200


def test_앱_회원_토큰은_막힌다(client: TestClient) -> None:
    token = create_access_token(uuid.uuid4(), SubjectType.APP, None)
    got = client.get("/admin/status", headers={"Authorization": f"Bearer {token}"})
    assert got.status_code == 401


# ---------------------------------------------------------------- 한 항목이 죽어도 200
def test_항목_하나가_timeout_이어도_200_이고_나머지가_온다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """카드의 작업 목록이 콕 집어 요구한 것.

    `ITEM_TIMEOUT_SEC` 를 짧게 줄여서 테스트가 실제로 2초를 기다리지 않게 한다.
    """
    async def _hangs() -> tuple[StatusState, str]:
        await asyncio.sleep(10)
        raise AssertionError("여기 오면 안 된다")

    monkeypatch.setattr(status_service, "ITEM_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(status_service, "_place", _hangs)

    items = _items(client)
    assert items["place"]["state"] == StatusState.DOWN
    assert "안 답하지" in items["place"]["detail"] or "답하지" in items["place"]["detail"]
    # 나머지는 멀쩡해야 한다 — 하나가 화면을 통째로 못 막는다는 것이 요점이다.
    assert items["db"]["state"] == StatusState.OK
    assert items["journey"]["state"] == StatusState.ABSENT


def test_항목이_예외를_던져도_200_이고_그_항목만_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom() -> tuple[StatusState, str]:
        raise RuntimeError("어딘가 터졌다")

    monkeypatch.setattr(status_service, "_redis", _boom)
    items = _items(client)
    assert items["redis"]["state"] == StatusState.DOWN
    assert "RuntimeError" in items["redis"]["detail"]
    assert items["db"]["state"] == StatusState.OK


def test_DB_가_죽으면_크롤은_묻지_않는다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 이유로 실패할 것을 두 번 물어 화면에 같은 에러를 두 줄 띄우지 않는다."""
    async def _dead(_session) -> tuple[StatusState, str]:
        raise RuntimeError("연결 안 됨")

    def _never(_session):
        raise AssertionError("DB 가 죽었으면 크롤은 묻지 않아야 한다")

    monkeypatch.setattr(status_service, "_db", _dead)
    monkeypatch.setattr(status_service, "_crawl", _never)

    items = _items(client)
    assert items["db"]["state"] == StatusState.DOWN
    assert items["crawl"]["state"] == StatusState.DOWN


# ---------------------------------------------------------------- 예열
@pytest.mark.parametrize(
    ("phase", "state"),
    [
        (WarmUpPhase.READY, StatusState.OK),
        (WarmUpPhase.LOADING, StatusState.DEGRADED),
        # **고장이 아니다.** 예열을 끈 개발 PC 에서는 첫 `/life/ask` 가 로드를 무는 것이 설계다.
        (WarmUpPhase.DISABLED, StatusState.ABSENT),
        # `ml` 그룹이 없을 때. `/life/ask` 만 죽고 다른 API 는 멀쩡하다 (D-021).
        (WarmUpPhase.FAILED, StatusState.DOWN),
    ],
)
def test_예열_상태가_그대로_옮겨진다(
    client: TestClient, phase: WarmUpPhase, state: StatusState
) -> None:
    from daengs_backend.main import app

    _warm_up(app, phase)
    assert _items(client)["warm-up"]["state"] == state


def test_예열_기록이_없으면_모른다고_말한다(client: TestClient) -> None:
    """lifespan 을 안 거치고 뜬 프로세스. `ok` 라고 하면 그게 제일 나쁘다."""
    from daengs_backend.main import app

    if hasattr(app.state, STATE_ATTR):
        delattr(app.state, STATE_ATTR)
    assert _items(client)["warm-up"]["state"] == StatusState.DEGRADED


def test_상태_라우터는_daengs_life_를_import_하지_않는다() -> None:
    """접점은 `main.py` 와 어댑터 둘뿐이다 (D-035).

    `tests/test_main_stays_light.py` 가 저장소 전체로 같은 것을 보지만, 여기서 한 번 더
    보는 이유는 **이 카드가 그 선을 건드릴 뻔했기 때문**이다 — 예열 상태의 원본이
    `daengs_life.app.deps` 에 있어서, 상태를 넣자는 요구가 곧장 그 import 로 간다.
    깨졌을 때 어느 카드가 그랬는지 여기서 바로 읽힌다.
    """
    package = Path(status_service.__file__).parents[1]
    for name in ("routers/status.py", "services/status.py", "schemas/status.py",
                 "core/warm_up.py"):
        # **AST 로 본다** — 주석에 `daengs_life` 라고 적는 것은 괜찮다. 오히려 이 파일들은
        # "왜 저기를 안 부르는가"를 주석으로 설명하고 있어서, 문자열로 찾으면 전부 걸린다.
        tree = ast.parse((package / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("daengs_life"), f"{name}: {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("daengs_life"), f"{name}: {alias.name}"


# ---------------------------------------------------------------- Redis 예산 키 계약
def test_예산_키_모양이_life_쪽_원본과_같다() -> None:
    """접점을 안 늘리려고 키를 두 군데에 적은 대가를 여기서 치른다.

    어긋나면 **예외가 하나도 안 난다** — 없는 키를 읽어 0 이 나오고, 화면은 "오늘 한 번도
    안 썼다"고 말한다. 임베딩 모델 불일치가 조용히 틀리는 것과 같은 종류다.
    """
    from daengs_life.realtime.cache import PREFIX, RedisStore

    assert status_service.BUDGET_KEY_PREFIX == PREFIX
    made = RedisStore(None)._budget_key("datagokr-warning", "20260903")
    assert made == f"{status_service.BUDGET_KEY_PREFIX}:budget:datagokr-warning:20260903"


def test_예산_그룹과_한도가_life_쪽_정책과_같다() -> None:
    """`cache.yaml` 의 `budgets` 를 따라 적은 것이다. 한도가 바뀌면 여기서 잡힌다."""
    from daengs_life.realtime.cache import POLICY

    for group, limit in status_service.DAILY_BUDGETS.items():
        assert POLICY.budgets.get(group) == limit, group
    # 한도가 `null` 인 것(apihub · kakao)은 일부러 뺐다 — "얼마나 남았나"를 말할 수 없다.
    assert {g for g, v in POLICY.budgets.items() if v is not None} == set(
        status_service.DAILY_BUDGETS
    )


# ---------------------------------------------------------------- 예외 문구
def test_메시지가_빈_예외는_콜론만_남기지_않는다() -> None:
    """`httpx.ConnectTimeout` 은 `str(e)` 가 **빈 문자열**이다.

    그대로 이어 붙이면 화면에 `ConnectTimeout:` 처럼 콜론만 남는다 — 2026-09-03 에
    로컬 콘솔에서 실제로 그렇게 떴다. 종류 이름만으로도 원인이 읽히므로 그때는 이름만 쓴다.
    """
    import httpx

    assert status_service._why(httpx.ConnectTimeout("")) == "ConnectTimeout"
    assert status_service._why(httpx.ConnectError("연결 거부")) == "ConnectError: 연결 거부"
    assert status_service._why(RuntimeError("어딘가 터졌다")) == "RuntimeError: 어딘가 터졌다"
