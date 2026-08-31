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
import uuid
from datetime import datetime, timezone

from daengs_gait.config import RECORDS_DIR


def _ensure_dir() -> None:
    # import 시점이 아니라 쓰는 시점에 만듭니다 — import 만으로 폴더가 생기면
    # 테스트나 CLI 가 엉뚱한 자리에 디렉터리를 남깁니다.
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)


def save_record(record: dict) -> str:
    _ensure_dir()
    record_id = record.get("record_id") or uuid.uuid4().hex[:8]
    record["record_id"] = record_id
    record.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    path = RECORDS_DIR / f"{record_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return record_id


def load_record(record_id: str) -> dict:
    path = RECORDS_DIR / f"{record_id}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def record_exists(record_id: str) -> bool:
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
    return sorted(records, key=lambda r: r.get("date") or r.get("created_at") or "")
