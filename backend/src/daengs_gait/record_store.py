"""분석 기록 저장 — 지금은 JSON 파일입니다.

walk_demo 의 동작을 그대로 옮겼습니다. DAENGS 에는 PostgreSQL + SQLAlchemy 가 이미
있지만, **이 서비스가 DB 를 직접 볼지는 아직 정하지 않았습니다** (README 의 TBD).
피부 병변 스크리닝이 DB 를 안 보는 무상태 서비스인 것과 같은 자리이고, 기록의 주인을
`daengs_backend` 로 둘지 여기로 둘지가 그 결정입니다.

그때까지는 파일이고, 파일 위치는 `GAIT_DATA_DIR` 로 바깥에서 정합니다 — 컨테이너에서
볼륨으로 물릴 자리입니다.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from daengs_gait.config import RECORDS_DIR


def _ensure_dir() -> None:
    # import 시점이 아니라 쓰는 시점에 만듭니다 — import 만으로 폴더가 생기면
    # 테스트나 CLI 가 엉뚱한 자리에 디렉터리를 남깁니다.
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)


# `record_id` 형식. **32자 소문자 16진수**(`uuid4().hex`)입니다.
#
# ⚠️ 예전에는 `uuid4().hex[:8]` 이었습니다 — 32비트라 수천 건이면 생일 문제로 충돌하고,
#    `save_record` 가 존재 확인 없이 덮어써서 **다른 개의 기록이 조용히 사라집니다.**
#    앱이 붙기 전에 늘렸습니다(API.md v1). 짧은 id 로 만든 기록이 남아 있어도 읽기는
#    되지만, 새로 만드는 것은 전부 32자입니다.
#
# 경로에 그대로 들어가는 값이라 **검증도 이 정규식으로 합니다** — 이것이 없으면
# `..\..\` 같은 값이 RECORDS_DIR 를 벗어납니다 (윈도우 개발 실행에서 실제로 뚫립니다).
RECORD_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


def is_valid_record_id(record_id: str) -> bool:
    """경로로 쓰기 전에 부르세요. 형식이 아니면 파일시스템을 건드리지 않습니다."""
    return bool(RECORD_ID_RE.fullmatch(record_id or ""))


def save_record(record: dict) -> str:
    _ensure_dir()
    record_id = record.get("record_id") or uuid.uuid4().hex
    record["record_id"] = record_id
    record.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    path = RECORDS_DIR / f"{record_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return record_id


def delete_record(record_id: str) -> tuple[dict, dict]:
    """기록 JSON 과 딸린 영상 파일을 **즉시 지웁니다.**

    반환: `(deleted, errors)` — 무엇을 지웠고 무엇이 실패했는지.
    `deleted` 는 `{"record": bool, "original": bool, "overlay": bool}` 이고,
    애초에 없던 파일은 **`None`** 입니다 (지울 게 없었던 것과 실패를 구분합니다).

    ⚠️ **부분 실패를 성공으로 감추지 않습니다.** 개인 데이터 삭제라 파일이 남았으면
       부르는 쪽이 그것을 알아야 합니다 — `service.py` 가 그때 500 을 냅니다.

    ⚠️ **파일 경로는 기록 안에만 있습니다.** `record_id` 와 업로드 파일 이름이 서로 다른
       uuid 라(`video_intake.save_upload` 가 따로 만듭니다) 여기서 JSON 을 먼저 읽어야
       무엇을 지울지 압니다. 그래서 **기록 JSON 을 마지막에 지웁니다** — 먼저 지우면
       영상 경로를 잃어버려 파일이 영영 고아가 됩니다.
    """
    deleted: dict = {"record": None, "original": None, "overlay": None}
    errors: dict = {}

    try:
        record = load_record(record_id)
    except FileNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001 — 깨진 JSON 이어도 파일은 지울 수 있어야 합니다
        record = {}
        errors["record_read"] = str(exc)

    for key, field in (("original", "original_video"), ("overlay", "overlay_video")):
        raw = record.get(field)
        if not raw:
            continue                      # 애초에 없음 — None 으로 남깁니다
        try:
            path = Path(raw)
            if path.exists():
                path.unlink()
                deleted[key] = True
            else:
                deleted[key] = None       # 이미 없음. 실패가 아닙니다
        except OSError as exc:
            deleted[key] = False
            errors[key] = str(exc)

    try:
        (RECORDS_DIR / f"{record_id}.json").unlink()
        deleted["record"] = True
    except FileNotFoundError:
        deleted["record"] = None
    except OSError as exc:
        deleted["record"] = False
        errors["record"] = str(exc)

    return deleted, errors


def load_record(record_id: str) -> dict:
    path = RECORDS_DIR / f"{record_id}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def record_exists(record_id: str) -> bool:
    """⚠️ **형식 검증이 여기에 있습니다.** 모든 엔드포인트가 파일을 만지기 전에 이걸
    먼저 부르므로, 여기서 막으면 `..\\..\\` 같은 값이 `RECORDS_DIR` 를 벗어나지
    못합니다. 컨테이너(리눅스)에서는 라우터가 슬래시를 안 받아 404 로 막히지만,
    README 가 안내하는 **윈도우 개발 실행에서는 역슬래시로 뚫립니다.**
    """
    if not is_valid_record_id(record_id):
        return False
    return (RECORDS_DIR / f"{record_id}.json").exists()


def list_records() -> list:
    if not RECORDS_DIR.exists():
        return []
    return sorted(p.stem for p in RECORDS_DIR.glob("*.json"))


def records_for_dog(dog_id: str) -> list:
    """`dog_id` 가 같은 기록만 날짜순으로.

    ⚠️ 파일 전수 순회입니다. 파일럿 규모(개체 수 · 세션 수가 적음)에서만 성립하고,
       기록이 쌓이면 DB 로 옮겨야 하는 자리입니다.

    ⚠️ **`dog_id` 는 넘어온 값을 그대로 믿습니다.** 두 기록이 정말 같은 개인지 검증하는
       로직이 없습니다 — 계정·반려견 프로필과 엮어 서버에서 강제할지는 아직 정하지
       않았습니다 (README 의 TBD).
    """
    records = [load_record(rid) for rid in list_records()]
    records = [r for r in records if r.get("dog_id") == dog_id]
    return sorted(records, key=_sort_key)


def _sort_key(record: dict) -> tuple:
    """오래된 것부터. 시간 변화를 보는 서비스라 시계열 순서가 자연스럽습니다.

    `date`(사용자가 준 촬영일)를 먼저 보고 없으면 `created_at`. 둘 다 없으면 맨 앞으로
    가되 `record_id` 로 순서를 고정합니다 — **정렬이 흔들리면 커서 페이지네이션이
    항목을 건너뛰거나 두 번 냅니다.**
    """
    return (record.get("date") or record.get("created_at") or "", record.get("record_id") or "")


def summarize(record: dict) -> dict:
    """목록에 담을 **요약만** 뽑습니다.

    ⚠️ **`trajectories` 와 `features` 는 담지 않습니다.** 한 건이 15KB 라 20건이면
       300KB 가 되고, 목록 화면이 쓰지 않는 값입니다. 특히
       `features.internal_feature_vector` 는 **UI 노출 금지** 값입니다 (CLAUDE.md) —
       수백 개의 숫자가 화면에 나오면 사용자가 건강 점수로 읽습니다.

    담는 이유가 덜 자명한 둘:
      · `comparable`          — `/v1/compare` 대상이 될 수 있는가. 비교 화면에서 고를
                                수 있는 것만 보여주는 데 씁니다
      · `gait_filter_version` — 버전이 다른 두 기록을 비교하면 경고가 붙습니다.
                                **고르기 전에** 앱이 알 수 있어야 합니다
    """
    quality = record.get("quality") or {}
    status = quality.get("status")
    record_id = record.get("record_id")
    return {
        "record_id": record_id,
        "date": record.get("date"),
        "created_at": record.get("created_at"),
        "source_file": record.get("source_file"),
        "note": record.get("note"),
        "quality_status": status,
        # unavailable 이면 tier 자체가 없습니다 (품질 판정 전에 끊긴 것).
        "quality_tier": quality.get("quality_tier"),
        "gait_filter_version": record.get("gait_filter_version"),
        "has_overlay": bool(record.get("overlay_video")),
        "comparable": status == "ok",
    }
