"""오케스트레이션 — 영상 하나를 기록으로, 기록 둘을 비교로.

    영상 입력 → 품질 체크 → keypoint 추론 → skeleton overlay
              → trajectory → feature → 기록 저장 → (선택) 기록 비교

품질 체크에서 `unavailable` 이면 **그 뒤 단계를 전부 건너뜁니다.** 검출되지 않은 것을
억지로 feature 로 만들면 숫자는 나오지만 아무 뜻이 없고, 그 숫자가 비교에 들어가면
없는 변화를 있다고 말하게 됩니다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from daengs_gait.compare import compare_loaded_records
from daengs_gait.config import GAIT_FILTER_VERSION, OVERLAYS_DIR, POSE_MODEL_ID
from daengs_gait.features import build_features
from daengs_gait.keypoint_infer import run_keypoint_inference
from daengs_gait.overlay import OverlayEncodeError, render_overlay_video
from daengs_gait.quality_gate import check_quality
from daengs_gait.record_store import load_record, save_record
from daengs_gait.trajectory import build_trajectories


def process_video(
    video_path,
    date: str | None = None,
    note: str | None = None,
    dog_id: str | None = None,
    original_filename: str | None = None,
    *,
    persist: bool = True,
) -> dict:
    """영상 하나 → 보행 기록 (이미 저장된 상태로 반환).

    `dog_id` 는 같은 개체의 기록을 묶기 위한 선택 필드입니다. 없어도 동작합니다.

    `original_filename` 은 사용자가 올린 원본 이름입니다. 디스크의 `video_path` 는
    uuid 이름이라 화면에 보여줄 수 없어서 따로 받습니다. **반드시 저장 전에 넣어야
    합니다** — 저장 뒤에 반환된 dict 만 고치면 응답과 저장본이 갈라져, 앱이 새로고침할
    때 이름이 uuid 로 바뀝니다.
    """
    video_path = Path(video_path)

    records, meta = run_keypoint_inference(video_path)
    quality = check_quality(records)

    record = {
        "record_id": None,
        # 화면에 보여줄 이름. 원본 이름을 못 받았을 때만 디스크의 uuid 이름으로 떨어집니다.
        "source_file": original_filename or video_path.name,
        # 저장된 원본 파일 경로 (uuid 이름). 화면에 보여줄 이름은 source_file 이고,
        # 이 경로는 원본 재생·향후 삭제 시 지울 대상을 가리키는 용도입니다.
        "original_video": str(video_path),
        "date": date,
        "note": note,
        "dog_id": dog_id,
        "created_at": datetime.now(UTC).isoformat(),
        # 어떤 관절 정의로 만든 기록인가 (D-063). 품질 판정 **앞**에 넣습니다 — unavailable
        # 이어도 엔진은 돌았으니 값이 있고, backend 가 그대로 gait_records.pose_model 에 씁니다.
        "pose_model": POSE_MODEL_ID,
        "video_meta": {
            "resolution": f"{meta['width']}x{meta['height']}",
            "native_fps": round(meta["native_fps"], 2),
        },
        "quality": quality,
        # 판정 로직의 버전. 다른 버전으로 만들어진 기록끼리 비교하면 같은 영상이라도
        # 수치가 달라 보이므로 compare_records() 가 이 값으로 경고를 붙입니다.
        "gait_filter_version": GAIT_FILTER_VERSION,
    }

    if quality["status"] != "ok":
        if persist:
            record_id = save_record(record)
            record["record_id"] = record_id
        return record

    # overlay 는 **곁딸린 산출물**입니다. 인코딩이 실패했다고 분 단위가 걸린 추론 결과를
    # 통째로 버리지는 않습니다. 다만 실패했으면 `overlay_video` 를 **넣지 않습니다** —
    # 넣으면 기록이 없는 영상을 있다고 광고하고, 사용자는 404 만 받습니다.
    # D-043 Celery 워커는 PostgreSQL/storage 가 원장이라 worker volume 사본을 남기지
    # 않습니다. persist=False 면 task 임시 입력 옆에 만들고 호출자가 bytes 를 읽은 뒤
    # TemporaryDirectory 가 원본·overlay 를 함께 없앱니다. legacy HTTP 서비스는 기존대로
    # GAIT_DATA_DIR 에 JSON과 overlay 를 보존합니다.
    overlay_dir = OVERLAYS_DIR if persist else video_path.parent
    overlay_path = overlay_dir / f"{video_path.stem}_overlay.mp4"
    overlay_video = None
    overlay_error = None
    try:
        overlay_video = render_overlay_video(video_path, records, overlay_path)
    except OverlayEncodeError as exc:
        overlay_error = str(exc)

    trajectories = build_trajectories(records)
    features = build_features(records)

    record.update({
        "overlay_video": overlay_video,
        # 실패했을 때만 채워집니다. 왜 overlay 가 없는지 기록에 남겨 두는 자리입니다.
        "overlay_error": overlay_error,
        "trajectories": trajectories,
        "features": features,
    })

    if persist:
        record_id = save_record(record)
        record["record_id"] = record_id
    return record

def compare_records(record_id_a: str, record_id_b: str) -> dict:
    """두 기록 비교 — **파일에서 읽는 옛 경로** (`/gait/compare`).

    판정은 [compare_loaded_records] 가 합니다. 여기는 기록을 불러오기만 하는 얇은 겉면이라,
    저장 위치가 바뀌어도 판정 코드가 따라 움직이지 않습니다.
    """
    return compare_loaded_records(load_record(record_id_a), load_record(record_id_b))

# 판정은 `daengs_gait.compare` 로 옮겼습니다 (위 compare_records 가 그것을 부릅니다).
