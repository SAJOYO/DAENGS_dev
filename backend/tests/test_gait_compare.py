"""보행 비교 `/app/gait/compare` (D-058).

⚠️ **DB 데이터만으로 완결되는지**가 이 파일의 주제입니다. 원본·overlay 파일을 한 번도
   만지지 않아야 저장소 구현(local·gcs·미설정)과 무관하게 비교가 돕니다.

판정 자체(30% 임계값·문구)는 `daengs_gait.compare` 것이고 여기서 바꾸지 않습니다 —
여기서 보는 것은 **소유권·비교 규칙·응답 계약**입니다.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.main import app
from daengs_backend.repositories import gait_record as gait_repo
from daengs_backend.services import gait as gait_service

OWNER = uuid.uuid4()
PET = uuid.uuid4()
OTHER_PET = uuid.uuid4()


def _rec(rid=None, *, pet=PET, day=1, tier="good", ver="v5", x=10.0, status="ok"):
    """비교가 읽는 필드만 가진 기록 대역 — DB 행과 같은 모양."""
    return type("R", (), {
        "id": rid or uuid.uuid4(),
        "pet_id": pet,
        "captured_at": datetime.date(2026, 9, day),
        "created_at": datetime.datetime(2026, 9, day, tzinfo=datetime.UTC),
        "quality": {"status": status, "quality_tier": tier, "recommendation": "다시 찍어 주세요"},
        "summary_for_ui": {"Hock": {"x_range": x, "y_range": 10.0}},
        "internal_feature_vector": {"f0": 1.0, "f1": 2.0},
        "gait_filter_version": ver,
    })()


@pytest.fixture()
def client():
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=OWNER)
    app.dependency_overrides[get_session] = lambda: object()
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def _pair(monkeypatch, rows):
    async def fake(session, app_user_id, ids):
        assert app_user_id == OWNER          # 소유권이 토큰 주인으로 확인되는지
        return rows

    monkeypatch.setattr(gait_repo, "get_owned_pair", fake)


def _post(client, a, b):
    return client.post(
        "/app/gait/compare", json={"record_id_a": str(a), "record_id_b": str(b)}
    )


# ── A·B 두 진입이 같은 계약을 쓴다 ──────────────────────────────────────
def test_compares_two_records_and_keeps_x_y_separate(client, monkeypatch):
    """**x·y 를 합치지 않습니다.** 합치면 어느 축이 움직였는지가 사라집니다 (D-058)."""
    a, b = _rec(day=1, x=10.0), _rec(day=5, x=20.0)   # x 는 100% 차이, y 는 동일
    _pair(monkeypatch, [a, b])

    r = _post(client, a.id, b.id)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    note = body["joint_movement_range_comparison"]["Hock"]["comparison_note"]
    assert note == {"x": "차이 관찰됨", "y": "비슷함"}


def test_order_does_not_matter_older_is_past(client, monkeypatch):
    """앱이 어느 순서로 고르든 같은 결과여야 합니다 — 정렬은 서버가 합니다."""
    old, new = _rec(day=1), _rec(day=9)
    for rows in ([old, new], [new, old]):
        _pair(monkeypatch, rows)
        body = _post(client, old.id, new.id).json()
        assert body["record_a"]["record_id"] == str(old.id)   # past
        assert body["record_b"]["record_id"] == str(new.id)   # recent


def test_dev_only_fields_never_reach_the_app(client, monkeypatch):
    """`_dev_only_*` 는 노출 금지 — 사용자가 건강 점수로 읽습니다."""
    a, b = _rec(day=1), _rec(day=2)
    _pair(monkeypatch, [a, b])
    body = _post(client, a.id, b.id).json()
    assert not [k for k in body if k.startswith("_dev_only")]


# ── 차단 규칙 ───────────────────────────────────────────────────────────
def test_same_record_is_rejected(client, monkeypatch):
    """같은 기록끼리는 400 — 존재 여부가 새지 않는 요청 오류입니다."""
    rid = uuid.uuid4()
    _pair(monkeypatch, [_rec(rid), _rec(rid)])
    assert _post(client, rid, rid).status_code == 400


def test_missing_or_others_record_is_404(client, monkeypatch):
    """없는 것과 남의 것을 구분하지 않습니다 — 구분하면 존재가 샙니다."""
    _pair(monkeypatch, [_rec()])          # 한 건만 잡힘 = 나머지는 없거나 남의 것
    assert _post(client, uuid.uuid4(), uuid.uuid4()).status_code == 404


def test_different_pets_are_rejected(client, monkeypatch):
    """소유자는 같아도 **다른 반려견**이면 비교가 의미를 잃습니다."""
    a, b = _rec(pet=PET), _rec(pet=OTHER_PET)
    _pair(monkeypatch, [a, b])
    r = _post(client, a.id, b.id)
    assert r.status_code == 400
    assert "반려견" in r.json()["detail"]


# ── 호환되지 않는 기록 ──────────────────────────────────────────────────
def test_not_ok_record_reports_unavailable_with_reason(client, monkeypatch):
    """분석이 안 된 기록은 200 + `unavailable` 입니다 — 오류가 아니라 상태입니다."""
    a, b = _rec(day=1), _rec(day=2, status="unavailable")
    _pair(monkeypatch, [a, b])
    body = _post(client, a.id, b.id).json()
    assert body["status"] == "unavailable"
    assert body["recommendation"] == "다시 찍어 주세요"


def test_version_mismatch_warns(client, monkeypatch):
    """필터 버전이 다르면 같은 영상이라도 이동범위가 달라 보입니다 — 경고를 답니다."""
    a, b = _rec(day=1, ver="v4"), _rec(day=2, ver="v5")
    _pair(monkeypatch, [a, b])
    assert _post(client, a.id, b.id).json()["version_warning"]


def test_low_tier_adds_reliability_note(client, monkeypatch):
    a, b = _rec(day=1, tier="low"), _rec(day=2, tier="low")
    _pair(monkeypatch, [a, b])
    assert _post(client, a.id, b.id).json()["reliability_note"]


# ── 저장소 독립 ─────────────────────────────────────────────────────────
def test_compare_never_touches_storage(client, monkeypatch):
    """**파일을 한 번도 안 만집니다.** 저장소가 미설정이어도 비교는 됩니다 —
    그래야 보관 방식(local·gcs)을 바꿔도 비교가 흔들리지 않습니다."""
    calls: list[str] = []

    class Boom:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError(f"compare 가 저장소를 만졌습니다: {name}")

    monkeypatch.setattr(gait_service, "get_storage", lambda: Boom())
    a, b = _rec(day=1), _rec(day=2)
    _pair(monkeypatch, [a, b])

    assert _post(client, a.id, b.id).status_code == 200
    assert calls == []
