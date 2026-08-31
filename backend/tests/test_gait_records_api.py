"""보행 기록 목록·삭제 API (`API.md` v1).

**가중치도 torch 도 없이 돕니다** — 기록 JSON 을 직접 만들어 넣고 조회·삭제만 봅니다.
분석 경로는 건드리지 않으므로 `daengs_gait.pipeline` 을 대역으로 바꿉니다
(`test_gait_service.py` 와 같은 장치).

여기서 지키는 것:
  · `record_id` 32자
  · 응답에 `/data/...` 내부 경로가 안 나가고 `overlay_url`·`has_overlay` 로 나감
  · 라우트가 버전 없는 `/analyze`·`/records` 이고, 앱이 보는 주소는 nginx 가 붙이는
    `/gait` 접두사가 앞에 옵니다 (`overlay_url` 이 그 기준입니다)
  · 목록이 `dog_id` 로만 걸러지고, 무거운 값(`features`·`trajectories`)을 안 담음
  · 삭제가 파일까지 지우고, **부분 실패를 성공으로 감추지 않음**
  · 기존 엔드포인트(단건 조회·compare)가 이번 변경으로 안 깨짐
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def gait(tmp_path, monkeypatch):
    """기록 저장소를 tmp 로 돌린 app + 헬퍼."""
    # 분석 경로의 무거운 import 를 막습니다 (torch 없이 돌기 위해).
    monkeypatch.setitem(sys.modules, "daengs_gait.pipeline", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "daengs_gait.video_intake", types.ModuleType("y"))

    from daengs_gait import config, record_store, service

    records_dir = tmp_path / "records"
    monkeypatch.setattr(config, "RECORDS_DIR", records_dir)
    monkeypatch.setattr(record_store, "RECORDS_DIR", records_dir)

    def make(record_id, dog_id, *, date=None, status="ok", tier="good", overlay=True):
        """기록 하나를 디스크에 만듭니다. 영상 파일도 같이 만듭니다."""
        records_dir.mkdir(parents=True, exist_ok=True)
        original = tmp_path / "uploads" / f"{record_id[:8]}.mp4"
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b"video")
        rec = {
            "record_id": record_id,
            "dog_id": dog_id,
            "date": date,
            "created_at": f"2026-08-{int(record_id[-2:], 16) % 28 + 1:02d}T00:00:00+00:00",
            "source_file": f"{record_id[:4]}.mp4",
            "note": None,
            "original_video": str(original),
            "quality": {"status": status, "quality_tier": tier if status == "ok" else None},
            "gait_filter_version": "v5-test",
        }
        if status == "ok":
            rec["features"] = {
                "summary_for_ui": {"hip": {"x_range": 1.0, "y_range": 0.5}},
                "internal_feature_vector": {f"f{i}": float(i) for i in range(121)},
            }
            rec["trajectories"] = [{"joint_name": "hip", "frames": [0], "x": [1.0], "y": [2.0]}]
            if overlay:
                ov = tmp_path / "overlays" / f"{record_id[:8]}_overlay.mp4"
                ov.parent.mkdir(parents=True, exist_ok=True)
                ov.write_bytes(b"overlay")
                rec["overlay_video"] = str(ov)
        (records_dir / f"{record_id}.json").write_text(
            json.dumps(rec, ensure_ascii=False), encoding="utf-8"
        )
        return rec

    return types.SimpleNamespace(
        client=TestClient(service.build_app()), make=make, dir=records_dir, tmp=tmp_path
    )


ID_A = "a" * 32
ID_B = "b" * 32
ID_C = "c" * 32


# --------------------------------------------------------------------------
# record_id 32자
# --------------------------------------------------------------------------
def test_new_record_id_is_32_hex(tmp_path, monkeypatch):
    """8자는 32비트라 수천 건에서 충돌하고, `save_record` 가 덮어써서 **다른 개의
    기록이 조용히 사라집니다.** 앱이 붙기 전에 늘렸습니다."""
    from daengs_gait import config, record_store

    monkeypatch.setattr(config, "RECORDS_DIR", tmp_path / "r")
    monkeypatch.setattr(record_store, "RECORDS_DIR", tmp_path / "r")

    rid = record_store.save_record({"dog_id": "d"})
    assert len(rid) == 32
    assert record_store.is_valid_record_id(rid)


def test_path_traversal_record_id_is_rejected(gait):
    """윈도우 개발 실행에서 역슬래시로 RECORDS_DIR 를 벗어나던 자리입니다."""
    from daengs_gait.record_store import record_exists

    for bad in ("..\\..\\secret", "../../secret", "a/b", "", "ZZZZ"):
        assert record_exists(bad) is False


# --------------------------------------------------------------------------
# 내부 경로가 응답에 안 나간다
# --------------------------------------------------------------------------
def test_single_record_hides_internal_paths(gait):
    """`/data/...` 는 앱에서 쓸 수 없고, 파일이 S3 로 가면 거짓이 됩니다."""
    gait.make(ID_A, "dog-1")
    body = gait.client.get(f"/records/{ID_A}").json()

    assert "original_video" not in body
    assert "overlay_video" not in body
    assert body["has_overlay"] is True
    # 앱이 그대로 붙여 쓸 수 있는 경로입니다 — nginx 가 떼는 `/gait` 접두사가 붙습니다.
    assert body["overlay_url"] == f"/gait/records/{ID_A}/overlay"


def test_overlay_url_is_null_when_absent(gait):
    gait.make(ID_A, "dog-1", overlay=False)
    body = gait.client.get(f"/records/{ID_A}").json()
    assert body["has_overlay"] is False
    assert body["overlay_url"] is None


# --------------------------------------------------------------------------
# 목록
# --------------------------------------------------------------------------
def test_list_requires_dog_id(gait):
    """`dog_id` 없는 전체 조회는 열지 않습니다 — 남의 기록이 다 보입니다."""
    assert gait.client.get("/records").status_code == 400


def test_list_filters_by_dog_and_sorts_oldest_first(gait):
    gait.make(ID_A, "dog-1", date="2026-08-20")
    gait.make(ID_B, "dog-2", date="2026-08-21")
    gait.make(ID_C, "dog-1", date="2026-08-10")

    body = gait.client.get("/records", params={"dog_id": "dog-1"}).json()
    ids = [r["record_id"] for r in body["records"]]

    assert ids == [ID_C, ID_A]          # 오래된 것부터
    assert ID_B not in ids              # 다른 개 기록이 안 섞임


def test_list_omits_heavy_and_forbidden_fields(gait):
    """`internal_feature_vector` 는 UI 노출 금지 값이고, 한 건 15KB 라 목록에 못 담습니다."""
    gait.make(ID_A, "dog-1")
    row = gait.client.get("/records", params={"dog_id": "dog-1"}).json()["records"][0]

    assert "features" not in row
    assert "trajectories" not in row
    assert not any("internal_feature_vector" in k or "_dev_only" in k for k in row)
    # 대신 담기로 한 것
    assert row["comparable"] is True
    assert row["gait_filter_version"] == "v5-test"
    assert row["has_overlay"] is True


def test_unavailable_record_is_not_comparable(gait):
    """비교 화면에서 고를 수 없는 것을 앱이 미리 알 수 있어야 합니다."""
    gait.make(ID_A, "dog-1", status="unavailable")
    row = gait.client.get("/records", params={"dog_id": "dog-1"}).json()["records"][0]
    assert row["comparable"] is False
    assert row["quality_tier"] is None


def test_list_paginates_with_cursor(gait):
    for i, rid in enumerate((ID_A, ID_B, ID_C)):
        gait.make(rid, "dog-1", date=f"2026-08-{10 + i:02d}")

    first = gait.client.get("/records", params={"dog_id": "dog-1", "limit": 2}).json()
    assert len(first["records"]) == 2
    assert first["next_cursor"] == first["records"][-1]["record_id"]

    second = gait.client.get(
        "/records",
        params={"dog_id": "dog-1", "limit": 2, "cursor": first["next_cursor"]},
    ).json()
    assert len(second["records"]) == 1
    assert second["next_cursor"] is None          # 더 없으면 null

    seen = [r["record_id"] for r in first["records"] + second["records"]]
    assert len(set(seen)) == 3                    # 건너뛰거나 겹치지 않음


def test_stale_cursor_is_rejected(gait):
    """지워진 기록을 커서로 주면 조용히 처음부터 주지 않습니다."""
    gait.make(ID_A, "dog-1")
    r = gait.client.get("/records", params={"dog_id": "dog-1", "cursor": ID_C})
    assert r.status_code == 400


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_bounds(gait, limit):
    gait.make(ID_A, "dog-1")
    r = gait.client.get("/records", params={"dog_id": "dog-1", "limit": limit})
    assert r.status_code == 400


def test_empty_list_is_not_an_error(gait):
    body = gait.client.get("/records", params={"dog_id": "nobody"}).json()
    assert body == {"records": [], "next_cursor": None}


# --------------------------------------------------------------------------
# 삭제
# --------------------------------------------------------------------------
def test_delete_removes_record_and_videos(gait):
    rec = gait.make(ID_A, "dog-1")
    original, overlay = Path(rec["original_video"]), Path(rec["overlay_video"])

    body = gait.client.delete(f"/records/{ID_A}").json()

    assert body["deleted"] == {"record": True, "original": True, "overlay": True}
    assert not original.exists()
    assert not overlay.exists()
    assert gait.client.get(f"/records/{ID_A}").status_code == 404


def test_delete_missing_record_is_404(gait):
    assert gait.client.delete(f"/records/{ID_C}").status_code == 404


def test_delete_reports_partial_failure_instead_of_silent_success(gait, monkeypatch):
    """⚠️ 개인 데이터 삭제라 **조용히 200 을 내면 안 됩니다.**

    사용자는 지워진 줄 알고 서버에는 영상이 남습니다.
    """
    gait.make(ID_A, "dog-1")

    real_unlink = Path.unlink

    def fail_on_overlay(self, *a, **kw):
        if "overlay" in self.name:
            raise OSError("장치가 사용 중입니다")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", fail_on_overlay)

    r = gait.client.delete(f"/records/{ID_A}")
    assert r.status_code == 500

    detail = r.json()["detail"]
    assert detail["deleted"]["overlay"] is False      # 무엇이 남았는지 말해 줍니다
    assert detail["deleted"]["original"] is True
    assert "overlay" in detail["errors"]


def test_delete_without_overlay_marks_it_none_not_failure(gait):
    """애초에 없던 파일은 실패가 아닙니다 — `None` 으로 구분합니다."""
    gait.make(ID_A, "dog-1", overlay=False)
    body = gait.client.delete(f"/records/{ID_A}").json()
    assert body["deleted"]["overlay"] is None
    assert body["deleted"]["record"] is True


# --------------------------------------------------------------------------
# 기존 엔드포인트 회귀
# --------------------------------------------------------------------------
def test_existing_endpoints_still_work(gait):
    """이번 변경으로 단건 조회·compare·404 가 깨지지 않았는지."""
    gait.make(ID_A, "dog-1")
    gait.make(ID_B, "dog-1")

    assert gait.client.get(f"/records/{ID_A}").status_code == 200
    assert gait.client.get(f"/records/{ID_C}").status_code == 404
    assert gait.client.get(f"/records/{ID_C}/overlay").status_code == 404

    # compare 는 없는 기록을 404 로 막는 것까지가 이 파일의 범위입니다
    # (계산 자체는 test_gait_inference.py 가 봅니다 — 거기는 모델 그룹이 필요합니다).
    r = gait.client.post("/compare", json={"record_id_a": ID_A, "record_id_b": ID_C})
    assert r.status_code == 404


def test_healthz_unchanged(gait):
    body = gait.client.get("/healthz").json()
    assert body["status"] == "ok"
    assert set(body["weights"]) == {"pose", "detector"}
