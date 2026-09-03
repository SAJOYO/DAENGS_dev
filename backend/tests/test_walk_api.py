"""`GET /life/walk-conditions` — ⑥ 응답 계약과 저하 경로 (RT-001 ⑥ · RAG-027, 구현 계획 9).

**검문소 D 를 한 층 위에서 다시 본다.** `test_collect.py` 는 실패가 판정까지 흘러가는지를
봤고, 여기는 그 판정이 **HTTP 계약으로 정직하게 나오는지**를 본다 — 죽은 축이 응답에서
`"unknown"` 으로 보이고, 왜 모르는지가 `sources` 에 남고, 필수 출처가 없으면 200 으로
"모른다"를 팔지 않는지.

시각과 캐시를 **둘 다 주입한다.** 안 하면 이 파일이 그날의 실제 시각과 그날 날씨를 따라가고,
내일 이유 없이 깨진다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daengs_life.app.deps import get_cache, get_now
from daengs_life.app.main import create_app
from daengs_life.app.services.walk import walk as walk_service
from daengs_life.realtime.cache import Cache, MemoryStore
from daengs_life.realtime.geo import LatLon
from daengs_life.realtime.providers import (
    airkorea_realtime, airkorea_stations, kakao_local, kma_apihub, kma_vilage_fcst, kma_warning,
)
from daengs_life.realtime.transport.base import Unavailable

FIXTURES = Path(__file__).parent / "fixtures" / "realtime"
HERE = LatLon(37.4979, 127.0276)
NOW = datetime.fromisoformat("2026-08-25T10:10:00+09:00")     # 픽스처 수집 시각 직후


def body(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["response"]["body"]


def raw(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def dead(*_a, **_k):
    raise Unavailable("죽었다", hint="검문소 D")


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 전부를 픽스처로. `test_collect.py` 와 같은 배선이다."""
    for module, name, value in (
        (kma_apihub, "raw_stations", text("kma-apihub.stations.txt")),
        (kma_apihub, "raw_aws", text("kma-apihub.aws.txt")),
        (airkorea_stations, "raw_list", body("airkorea-stations.list.json")),
        (kakao_local, "raw_region", raw("kakao-local.region.json")),
        (kma_vilage_fcst, "raw_ncst", body("kma-vilage-fcst.ncst.json")),
        (kma_vilage_fcst, "raw_ultra", body("kma-vilage-fcst.ultra.json")),
        (kma_vilage_fcst, "raw_village", body("kma-vilage-fcst.village.json")),
        (airkorea_realtime, "raw_dnsty", body("airkorea-realtime.dnsty.json")),
        (airkorea_realtime, "raw_frcst", body("airkorea-realtime.frcst.json")),
        (kma_warning, "raw_pwn", body("kma-warning.pwn.json")),
    ):
        monkeypatch.setattr(module, name, lambda *a, _v=value, **k: _v)


@pytest.fixture
def client(wired) -> TestClient:
    """캐시와 시계를 갈아끼운 앱. **`deps.py` 가 있는 이유가 이것이다** (RAG-027)."""
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: Cache(MemoryStore())
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def out(**_):
    return walk_service(HERE, NOW, cache=Cache(MemoryStore()))


# ------------------------------------------------------------------ ⑥ 계약

def test_the_response_carries_the_five_parts_the_contract_promised(client) -> None:
    """⑥ — 엔드포인트 하나가 `now` + `timeline` + `windows` + `sources` 를 타임라인째로 준다."""
    r = client.get("/life/walk-conditions", params={"lat": HERE.lat, "lon": HERE.lon})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) >= {"location", "generated_at", "now", "timeline", "windows", "sources", "notes"}
    assert d["now"]["grade"] in {"UNSAFE", "CAUTION", "GOOD"}
    assert len(d["timeline"]) == 25                      # T+0 + 24시간
    assert d["sources"], "저하 이유를 볼 자리가 비어 있다"


def test_the_label_is_built_by_the_server(wired) -> None:
    """§0 의 정직 표기. GPS 는 점인데 답은 측정소 단위라, 그 간극을 문장에 넣는다."""
    assert out().location.label == "서초2동 (측정소: 강남대로) 기준"


def test_only_now_carries_the_detailed_basis(wired) -> None:
    """⑥ — 타임라인 25개에 근거를 전부 실으면 응답이 몇 배가 된다."""
    result = out()
    assert any(axis.basis for axis in result.now.axes.values())
    assert not hasattr(result.timeline[0], "axes")


def test_the_derived_value_is_a_number_not_a_parsed_sentence(wired) -> None:
    """`note` 문구를 다듬는 날 API 가 깨지면 안 된다 — `derived` 가 그래서 있다."""
    heat = out().now.axes["heat"]
    assert isinstance(heat.derived.get("feels_like_c"), float)
    assert "체감온도" in heat.note


def test_an_interval_is_not_squashed_into_a_point(wired) -> None:
    """②-d-1 — `'강수없음'` 을 `0.0` 으로 누르면 없는 정밀도를 만든다."""
    basis = [b for axis in out().now.axes.values() for b in axis.basis]
    intervals = [b for b in basis if getattr(b, "unit_note", None) and "~" in (b.unit_note or "")]
    assert all(isinstance(b.value, str) for b in intervals), "구간이 숫자로 눌렸다"


# ------------------------------------------------- 타임라인·권장 구간의 정직성

def test_the_timeline_starts_now_but_then_lands_on_whole_hours(wired) -> None:
    """예보값은 시간 단위다. 12:49 · 13:49 로 찍으면 `windows` 가 "20:49부터 좋다"고 말한다."""
    line = out().timeline
    assert line[0].at == NOW
    assert all(p.at.minute == 0 and p.at.second == 0 for p in line[1:])
    assert line[-1].at <= NOW + timedelta(hours=25)


def test_a_window_reports_its_worst_hour_not_its_best(wired) -> None:
    """구간 안이 전부 같은 등급이라는 보장이 없다. 최고를 쓰면 실제보다 좋게 말하게 된다."""
    result = out()
    inside = {p.at: p.grade for p in result.timeline}
    for window in result.windows:
        hours = [g for at, g in inside.items() if window.from_ <= at <= window.to]
        assert window.grade in hours
        assert not (window.grade == "GOOD" and "CAUTION" in hours)


def test_the_dry_run_shape_survives_the_serialisation(wired) -> None:
    """③ 드라이런이 낸 그림 — **낮은 나쁘고 밤에 회복된다** — 이 계약을 통과해도 그대로인가.

    `now`(10:10)로 보지 않는 이유 — 드라이런이 `UNSAFE` 라고 한 것은 **오후**이고 오전은
    원래 `CAUTION` 이다. 현재 시각으로 단정하면 픽스처를 몇 시에 떴느냐에 테스트가 묶인다.
    """
    result = out()
    by_hour = {p.at.hour: p for p in result.timeline if p.at.day == 25}
    assert by_hour[15].grade == "UNSAFE" and by_hour[15].dominant == ["heat"]
    assert result.windows, "권장 구간이 하나도 없다"
    assert result.windows[0].from_.hour >= 18, "밤 회복 구간이 사라졌다"


# ---------------------------------------------- 검문소 D 를 계약 위에서 다시

def test_a_dead_airkorea_shows_up_as_unknown_and_never_as_good(client, monkeypatch) -> None:
    """**응답이 모르는 것을 모른다고 말하는가.** 여기서 삼키면 죽은 API 가 `GOOD` 이 된다."""
    monkeypatch.setattr(airkorea_realtime, "raw_dnsty", dead)
    monkeypatch.setattr(airkorea_realtime, "raw_frcst", dead)

    d = client.get("/life/walk-conditions", params={"lat": HERE.lat, "lon": HERE.lon}).json()
    assert "air" in d["now"]["unknown_axes"]
    assert d["now"]["axes"]["air"]["grade"] == "unknown"
    assert d["now"]["grade"] != "GOOD"
    failed = [s for s in d["sources"] if not s["ok"]]
    assert failed and any(s["reason"] for s in failed), "왜 모르는지가 응답에 없다"


def test_unknown_is_a_string_not_null(client, monkeypatch) -> None:
    """⑥ — `null` 은 "필드 없음"과 헷갈린다."""
    monkeypatch.setattr(airkorea_realtime, "raw_dnsty", dead)
    monkeypatch.setattr(airkorea_realtime, "raw_frcst", dead)
    d = client.get("/life/walk-conditions", params={"lat": HERE.lat, "lon": HERE.lon}).json()
    assert d["now"]["axes"]["air"]["grade"] == "unknown"


def test_no_verdict_is_a_503_that_still_explains_itself(client, monkeypatch) -> None:
    """⑤-a — 필수 출처가 통째로 없으면 200 으로 "모른다"를 팔지 않는다.

    그래도 **본문은 그대로 싣는다** — 어느 출처가 죽어서 답을 못 내는지 보이지 않으면
    사용자에게는 그냥 장애다.
    """
    for name in ("raw_ncst", "raw_ultra", "raw_village"):
        monkeypatch.setattr(kma_vilage_fcst, name, dead)
    monkeypatch.setattr(kma_apihub, "raw_aws", dead)

    r = client.get("/life/walk-conditions", params={"lat": HERE.lat, "lon": HERE.lon})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["now"]["grade"] == "unknown"
    assert [s for s in detail["sources"] if not s["ok"]]


def test_a_stale_source_is_visible_and_noted(wired, monkeypatch) -> None:
    """⑤-c — 옛 값을 쓰는 것은 정상 경로다. 다만 **말은 해야 한다**."""
    cache = Cache(MemoryStore())
    walk_service(HERE, NOW, cache=cache)                     # 한 번 채운다
    monkeypatch.setattr(kma_vilage_fcst, "raw_village", dead)   # 그다음 죽인다
    later = walk_service(HERE, NOW + timedelta(hours=4), cache=cache)
    stale = [s for s in later.sources if s.stale]
    assert stale, "옛 값을 썼는데 stale 로 안 보인다"
    assert any("옛 값" in n for n in later.notes)


def test_a_dead_kakao_still_answers(client, monkeypatch) -> None:
    """①이 카카오를 표기 역할로 좁힌 것 — 표기 때문에 판정을 못 주면 안 된다."""
    monkeypatch.setattr(kakao_local, "raw_region", dead)
    d = client.get("/life/walk-conditions", params={"lat": HERE.lat, "lon": HERE.lon}).json()
    assert d["now"]["grade"] != "unknown"
    assert d["location"]["label"].startswith("37.4979")       # 좌표를 그대로 쓴다


# ------------------------------------------------------------------ HTTP 경계

@pytest.mark.parametrize("params", [
    {"lat": 51.5, "lon": 0.1},        # 런던 — 격자 변환은 되지만 API 가 빈 응답을 준다
    {"lat": 37.5},                    # lon 없음
    {"lat": "여기", "lon": 127.0},
])
def test_a_coordinate_we_cannot_serve_is_rejected_at_the_edge(client, params) -> None:
    """경계에서 끊는다. 안 끊으면 "판정 불가"가 되는데 원인이 좌표라는 걸 알 수 없다."""
    assert client.get("/life/walk-conditions", params=params).status_code == 422


def test_the_root_route_still_answers(client) -> None:
    """기존 `main.py` 가 갖고 있던 것 — shim 으로 옮기면서 안 잃었는지."""
    assert client.get("/").json() == {"Daengs": "Life Assistant"}


# `test_the_shim_exposes_the_same_app` 은 이관하며 뺐다 (D-018). `backend/main.py` 2줄 shim 은
# `feat/rag` 와 `feat/realtime` 이 병렬로 살아 있는 동안 rename-vs-modify 충돌을 막으려고
# 둔 것이었고(RAG-027), 그 조건이 이 이관으로 끝났다. 이 레포의 ASGI 진입점은
# `daengs_backend.main` 이고 실행은 `uv run dev` 다 — `fastapi dev main.py` 를 쓰지 않는다.
