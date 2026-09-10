"""realtime 전용 앱과 분리 갈림길의 회귀 가드 (D-068).

**왜 파일 하나인가** — 이 셋은 같은 결정의 세 면이다: 앱이 realtime 만 담는가, 갈림길이
기본값에서 옛 경로로 가는가, 프록시가 503 본문을 안 뭉개는가. 따로 두면 하나를 고칠 때
나머지 둘을 안 보게 된다.

**기본값에서 옛 경로여야 한다는 단언이 여기 있는 이유** — `DAENGS_REALTIME_URL` 이 비어
있으면 이 저장소의 동작은 분리 전과 **글자 그대로 같아야** 한다. 그것이 되돌리기를 환경변수
하나로 만드는 조건이고, 그 조건이 깨지면 개발 PC·개발서버가 조용히 다른 것을 부른다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI


def _routes(app: FastAPI) -> dict[str, object]:
    """`{경로: 핸들러}` 전부.

    ⚠ **`app.routes` 를 그냥 훑으면 안 된다.** fastapi 0.141 부터 `include_router` 가
    라우트를 앱에 평탄화하지 않고 `fastapi.routing._IncludedRouter` **하나로 감싼다** —
    원본은 그 객체의 `original_router` 에 있다. 옛 방식(`{r.path for r in app.routes}`)으로
    세면 **등록한 라우터가 통째로 안 보이고**, 그래도 `AttributeError` 가 안 나서 단언이
    "등록이 안 됐다"로 조용히 뒤집힌다 (2026-09-11 실측, fastapi 0.141.1).

    `app.openapi()["paths"]` 로도 경로는 나오지만 **핸들러가 어느 모듈 것인지**는 안 나온다.
    갈림길의 기본값을 확인하려면 그것이 필요해서 여기서 직접 훑는다.
    """
    found: dict[str, object] = {}

    def collect(routes: object) -> None:
        for route in routes:  # type: ignore[union-attr]
            inner = getattr(route, "original_router", None)
            if inner is not None:
                collect(inner.routes)
                continue
            path = getattr(route, "path", None)
            if path is not None:
                found[path] = getattr(route, "endpoint", None)

    collect(app.routes)
    return found


def _paths(app: FastAPI) -> set[str]:
    return set(_routes(app))


# ---------------------------------------------------------------- realtime 전용 앱

def test_realtime_app_serves_exactly_the_two_realtime_routes() -> None:
    """`/life/ask` 가 들어오면 RAG 스택이 이미지에 필요해진다 — 그것이 이 단언의 요지다."""
    from daengs_life.app.realtime_main import app

    paths = _paths(app)
    assert "/life/walk-conditions" in paths
    assert "/weather/at" in paths
    assert "/life/ask" not in paths


def test_realtime_app_does_not_touch_the_encoder() -> None:
    """예열이나 인코더 조회가 들어오면 `ml` 없는 이미지에서 기동이 깨지거나 시끄러워진다."""
    import inspect

    from daengs_life.app import realtime_main

    source = inspect.getsource(realtime_main)
    assert "warm_up_encoder" not in source
    assert "get_encoder" not in source


# ---------------------------------------------------------------- HTTP 클라이언트

def test_realtime_url_defaults_to_empty_so_nothing_changes_by_default() -> None:
    from daengs_backend.config import Settings

    assert Settings().realtime_url == ""


def test_client_keeps_status_and_body_untouched_on_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/life/walk-conditions` 는 판정 불가일 때 503 **본문에 응답 전체**를 싣는다
    (`daengs_life` 의 `controllers/walk.py`). 프런트가 그것을 읽으므로 502 로 뭉개면 안 된다.
    """
    import httpx

    from daengs_backend.services import realtime_client

    body = {
        "detail": {
            "now": {"grade": "unknown"},
            "sources": [{"provider": "ncst", "ok": False, "reason": "예산 초과"}],
        }
    }

    def fake_send(self, request, **kwargs):  # noqa: ANN001, ANN003, ARG001
        return httpx.Response(503, json=body, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    monkeypatch.setattr(realtime_client, "_id_token", lambda audience: "test-token")

    status, payload = realtime_client.get_walk(
        37.4979, 127.0276, base_url="https://rt.example"
    )
    assert status == 503
    assert payload == body


def test_client_turns_transport_failure_into_its_own_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """전송 실패와 「실시간 서비스가 낸 오류 응답」을 갈라 둔다 — 원인이 섞이면 못 짚는다."""
    import httpx

    from daengs_backend.services import realtime_client

    def boom(self, request, **kwargs):  # noqa: ANN001, ANN003, ARG001
        raise httpx.ConnectTimeout("nope", request=request)

    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(realtime_client, "_id_token", lambda audience: "test-token")

    with pytest.raises(realtime_client.RealtimeUnavailable):
        realtime_client.get_walk(37.4979, 127.0276, base_url="https://rt.example")


def test_token_fetch_failure_also_becomes_realtime_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADC 조회 자체가 실패해도(메타데이터 서버가 안 닿는 등) 같은 예외로 닫혀야 한다.

    `_id_token` 을 통째로 갈아끼우면 이 자리를 안 지난다 — 그래서 다른 테스트들과 달리
    `google.oauth2.id_token.fetch_id_token_credentials` 를 직접 갈아끼워 실제 `_id_token`
    본문(캐시 조회 → 실패)을 지나가게 한다. 여기서 안 잡히면 raw `DefaultCredentialsError`
    가 새 나가는데, Task 4 의 프록시 라우터는 `RealtimeUnavailable` 만 502 로 잡으므로 이
    경로만 500 이 된다.
    """
    import google.auth.exceptions
    import google.oauth2.id_token

    from daengs_backend.services import realtime_client

    def boom(audience, request=None, **kwargs):  # noqa: ANN001, ANN003, ARG001
        raise google.auth.exceptions.DefaultCredentialsError("no ADC in test env")

    monkeypatch.setattr(google.oauth2.id_token, "fetch_id_token_credentials", boom)
    # 다른 테스트가 이 audience 로 자격증명을 캐시해 두면 이 테스트가 실제 조회를
    # 건너뛰고 거짓으로 통과한다 — audience 를 이 테스트 전용으로 갈라 둔다.
    monkeypatch.setattr(realtime_client, "_credentials", {})

    with pytest.raises(realtime_client.RealtimeUnavailable):
        realtime_client.get_walk(37.4979, 127.0276, base_url="https://token-fail.example")


# ---------------------------------------------------------------- 갈림길

def test_default_registration_still_uses_the_in_process_router() -> None:
    """기본값에서 라우트가 `daengs_life` 것이어야 한다 — 아니면 기존 인증 테스트가 거짓 통과한다."""
    from daengs_backend.config import settings
    from daengs_backend.main import app

    assert settings.realtime_url == ""
    walk_endpoint = _routes(app)["/life/walk-conditions"]
    assert walk_endpoint is not None
    assert walk_endpoint.__module__ == "daengs_life.app.controllers.walk"


def test_weather_lookup_reduces_http_body_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 갈래와 in-process 갈래가 **같은 `WeatherAtOut`** 을 보므로 원자 추출이 한 벌이다.

    이 단언이 지키는 것은 「분리 때문에 산책 기록에 박히는 값이 달라지지 않는다」다.
    """
    from daengs_backend.config import settings
    from daengs_backend.orchestration.adapters import life as life_adapter

    body = {
        "status": "captured",
        "requested_at": "2026-09-11T05:00:00+09:00",
        "fetched_at": "2026-09-11T05:10:00+09:00",
        "grid": [61, 125],
        "observations": [
            {
                "quantity": "temp_c",
                "representation": "number",
                "value": 21.5,
                "source": "ncst",
                "spatial_ref": "격자 61,125",
                "valid_at": "2026-09-11T05:00:00+09:00",
                "issued_at": "2026-09-11T05:00:00+09:00",
            }
        ],
        "sources": [{"provider": "ncst", "outcome": "ok", "calls": 1}],
    }

    monkeypatch.setattr(settings, "realtime_url", "https://rt.example")
    monkeypatch.setattr(
        "daengs_backend.services.realtime_client.post_weather_at",
        lambda payload, *, base_url: (200, body),
    )

    observed = datetime(2026, 9, 11, 5, 0, tzinfo=timezone.utc)
    out = life_adapter._weather_at_life(37.4979, 127.0276, observed)
    assert out.status == "captured"
    assert out.temperature_c == 21.5
