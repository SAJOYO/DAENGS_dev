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


LEGACY = "yolov8_12kp_best"
V4 = "rtmpose_ap10k_ssd"


def _rec(
    rid=None, *, pet=PET, day=1, tier="good", ver="v5", x=10.0, status="ok", pose_model=LEGACY
):
    """비교가 읽는 필드만 가진 기록 대역 — DB 행과 같은 모양.

    `pose_model` 기본은 legacy — 이 파일의 판정 케이스는 `daengs_gait.compare` 것이라서요.
    """
    return type("R", (), {
        "id": rid or uuid.uuid4(),
        "pet_id": pet,
        "captured_at": datetime.date(2026, 9, day),
        "created_at": datetime.datetime(2026, 9, day, tzinfo=datetime.UTC),
        "quality": {"status": status, "quality_tier": tier, "recommendation": "다시 찍어 주세요"},
        "summary_for_ui": {"Hock": {"x_range": x, "y_range": 10.0}},
        "internal_feature_vector": {"f0": 1.0, "f1": 2.0},
        "gait_filter_version": ver,
        "pose_model": pose_model,
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


# ── 판정 경계 — "차이 관찰됨" 은 임계값을 **넘을 때만** ────────────────────
#
# 5C 에서 계산을 두 엔진이 공유하게 되면서, 경계의 의미(`>` 인가 `>=` 인가)가 리팩터링에
# 조용히 뒤집힐 수 있는 자리가 됐습니다. 임계값 바로 아래·정확히·바로 위 셋을 다 봅니다.
# **숫자를 여기 옮겨 적지 않습니다** — 상수에서 유도해야 상수를 바꿔도 의미가 지켜집니다.
def _rel(va: float, vb: float) -> float:
    """`direction_note` 가 쓰는 상대 차이 — 경계값을 만들 때 같은 식을 씁니다."""
    return abs(va - vb) / max(abs(va), abs(vb), 1e-9)


def test_threshold_boundary_stays_strictly_greater():
    from daengs_gait.compare import direction_note
    from daengs_gait.config import COMPARE_DIFF_THRESHOLD as T

    base = 10.0
    below, at, above = base * (1 - T / 2), base * (1 - T), base * (1 - T * 1.5)

    # 경계값이 **정말** 경계인지 먼저 확인합니다. 부동소수 때문에 `at` 이 임계값에서
    # 미끄러지면 아래 단언이 경계를 안 보고 통과해 버립니다.
    assert _rel(below, base) < T
    assert _rel(at, base) == T
    assert _rel(above, base) > T

    assert direction_note(below, base) == "비슷함"
    assert direction_note(at, base) == "비슷함"          # 정확히 임계값이면 "비슷함"
    assert direction_note(above, base) == "차이 관찰됨"


def test_direction_note_does_not_say_which_way(client, monkeypatch):  # noqa: ARG001
    """**늘었는지 줄었는지는 말하지 않습니다.** 표본이 작을 때 관절별 비율이 크게 흩어지는
    것을 실측했고, 방향까지 단언하면 진단처럼 읽힙니다. 그래서 순서를 바꿔도 답이 같습니다."""
    from daengs_gait.compare import direction_note

    assert direction_note(4.0, 10.0) == direction_note(10.0, 4.0) == "차이 관찰됨"
    assert direction_note(9.5, 10.0) == direction_note(10.0, 9.5) == "비슷함"


def test_direction_note_when_one_side_has_no_value():
    """없는 값을 "비슷함" 으로 뭉치지 않습니다 — 데이터가 없는 것을 변화가 없다고 말하게 됩니다."""
    from daengs_gait.compare import direction_note

    assert direction_note(None, 10.0) == "비교 불가(한쪽 기록에 없음)"
    assert direction_note(10.0, None) == "비교 불가(한쪽 기록에 없음)"


@pytest.mark.parametrize(
    ("factor", "expected"),
    [(1.0, "비슷함"), (1.5, "차이 관찰됨")],  # 임계값의 1배(경계) · 1.5배
)
def test_threshold_boundary_reaches_the_app_response(client, monkeypatch, factor, expected):
    """경계 판정이 응답까지 그대로 실려 나가는지 — 계산이 맞아도 배선이 끊기면 소용없습니다."""
    from daengs_gait.config import COMPARE_DIFF_THRESHOLD as T

    base = 10.0
    a, b = _rec(day=1, x=base * (1 - T * factor)), _rec(day=5, x=base)
    _pair(monkeypatch, [a, b])

    body = _post(client, a.id, b.id).json()
    assert body["joint_movement_range_comparison"]["Hock"]["comparison_note"]["x"] == expected
    assert body["joint_movement_range_comparison"]["Hock"]["comparison_note"]["y"] == "비슷함"


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


# ── pose_model 호환성 (D-063 2단계) ──────────────────────────────────────
# 비교 함수는 서버의 GAIT_ENGINE 이 아니라 **두 기록의 pose_model** 로 고릅니다.
# 아래는 v4 compare 를 대역으로 갈아 끼워 어느 쪽이 불렸는지 봅니다 — 진짜 v4 compare 는
# test_gait_v4_engine.py 가 파일 로드로 검사합니다.


def _spy_v4_compare(monkeypatch):
    called: list[tuple[str, str]] = []

    def fake_v4(a, b):
        called.append((a["pose_model"], b["pose_model"]))
        return {"status": "ok", "message_for_ui": "v4", "_dev_only_x": 1}

    monkeypatch.setattr(gait_service, "_load_v4_compare", lambda: fake_v4)
    return called


def test_two_v4_records_use_v4_compare_and_get_200(client, monkeypatch):
    called = _spy_v4_compare(monkeypatch)
    a, b = _rec(day=1, pose_model=V4), _rec(day=2, pose_model=V4)
    _pair(monkeypatch, [a, b])

    r = _post(client, a.id, b.id)
    assert r.status_code == 200
    assert r.json()["message_for_ui"] == "v4"
    assert called == [(V4, V4)]  # _as_compare_record 가 pose_model 을 넘긴다


def test_two_legacy_records_use_legacy_compare_even_when_server_runs_v4(client, monkeypatch):
    """서버가 v4 엔진으로 돌아도 legacy 기록 둘은 legacy 판정입니다 — 기록이 기준입니다."""
    from daengs_backend.config import settings

    monkeypatch.setattr(settings, "gait_engine", "v4")
    called = _spy_v4_compare(monkeypatch)
    a, b = _rec(day=1, x=10.0), _rec(day=2, x=20.0)
    _pair(monkeypatch, [a, b])

    body = _post(client, a.id, b.id).json()
    assert called == []
    assert body["joint_movement_range_comparison"]["Hock"]["comparison_note"]["x"] == "차이 관찰됨"


def test_mixed_pose_models_are_rejected_with_400(client, monkeypatch):
    """legacy ↔ v4 는 관절 이름이 한 글자도 안 겹칩니다 — 비교 불가는 오류가 아니라 상태이고,
    "다른 반려견" 과 같은 통로(400 + 사유)로 나갑니다."""
    called = _spy_v4_compare(monkeypatch)
    a, b = _rec(day=1, pose_model=LEGACY), _rec(day=2, pose_model=V4)
    _pair(monkeypatch, [a, b])

    r = _post(client, a.id, b.id)
    assert r.status_code == 400
    assert "서로 다른 분석 모델" in r.json()["detail"]
    assert called == []


@pytest.mark.parametrize(
    ("pm_a", "pm_b"),
    [(None, V4), (LEGACY, None), (None, None)],
)
def test_null_pose_model_is_rejected_with_400(client, monkeypatch, pm_a, pm_b):
    """NULL 은 같은 모델로 보지 않습니다 — 둘 다 NULL 이어도 비교 불가입니다."""
    called = _spy_v4_compare(monkeypatch)
    a, b = _rec(day=1, pose_model=pm_a), _rec(day=2, pose_model=pm_b)
    _pair(monkeypatch, [a, b])

    r = _post(client, a.id, b.id)
    assert r.status_code == 400
    assert "분석 모델 정보가 없는" in r.json()["detail"]
    assert called == []


def test_unknown_pose_model_is_rejected_with_400(client, monkeypatch):
    called = _spy_v4_compare(monkeypatch)
    a, b = _rec(day=1, pose_model="future_model"), _rec(day=2, pose_model="future_model")
    _pair(monkeypatch, [a, b])

    r = _post(client, a.id, b.id)
    assert r.status_code == 400
    assert "지원하지 않는 분석 모델" in r.json()["detail"]
    assert called == []


def test_pose_model_check_comes_after_ownership_and_pet_rules(client, monkeypatch):
    """존재·소유권(404)과 다른 반려견(400) 이 먼저입니다 — 모델 검사로 존재가 새지 않게."""
    _pair(monkeypatch, [_rec(pose_model=None)])
    assert _post(client, uuid.uuid4(), uuid.uuid4()).status_code == 404

    a, b = _rec(pet=PET, pose_model=None), _rec(pet=OTHER_PET, pose_model=V4)
    _pair(monkeypatch, [a, b])
    assert "반려견" in _post(client, a.id, b.id).json()["detail"]
