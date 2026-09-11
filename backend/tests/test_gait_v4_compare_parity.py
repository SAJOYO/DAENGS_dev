"""v4 compare parity — 새 `daengs_gait.compare_v4` 가 옛 `backend/gait_v4/gait_v4/compare.py` 와
**내부 JSON 까지** 같은 답을 내는가 (D-063 5B, 추가 구현 조건 1).

옛 구현은 5B 에서 지워졌으므로 그 출력을 **미리 얼려 둔 픽스처**(`compare_v4_parity.json`,
dev `aef5adf0` 시점의 옛 `compare.py` 로 생성)와 대조합니다. HTTP 스키마(`GaitCompareResponse`)
통과 여부가 아니라 `message_kind` · `side_summary` · `condition_flags` · `_dev_only_*` 를 포함한
비교 함수의 반환값 전체입니다 — `_dev_only_*` 는 backend 가 앱으로 보내기 전에 걷어내는데,
그 걷어내기가 옛 것과 같은 키를 대상으로 하는지도 여기서 확정됩니다.

입력은 실제 v4 기록(`record_v4_rear.json`, 골든 영상)에서 파생한 7 케이스: 같은 기록 /
왼쪽만 다름 / 양쪽 다름 / 조건 플래그 셋+저품질 tier / 한쪽에만 있는 관절 / quality
unavailable / pose_model 불일치.

numpy 만 쓰는 모듈이라 모델·영상 없이 돕니다.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from daengs_backend.services import gait as gait_service
from daengs_gait.compare_v4 import compare_records

FIXTURE = Path(__file__).parent / "fixtures" / "gait" / "compare_v4_parity.json"


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


#: 옛 구현에는 없었고 **뒤에 더한** 키. 경로 끝 이름으로 적습니다.
#:
#: ⚠️ **여기에 이름을 더하는 것은 "옛 출력과 달라져도 된다"고 선언하는 일입니다.** 값이
#:    바뀌는 변경은 여기 적어도 통과하지 않습니다 — 이 목록은 *새 키*만 면제합니다.
#:    지금 하나뿐입니다:
#:    · `n_unmeasured` — 다리별 "못 잰 관절" 수 (D-063 7단계). 앱이 서버 판정을 쓰려면
#:      "비교할 관절이 부족함" 갈래를 서버 수로 판단할 수 있어야 해서 더했습니다.
#:      세기만 하고 `flagged` 규칙에는 안 들어갑니다 (`tests/test_gait_side_summary.py`).
ADDED_AFTER_PARITY = frozenset({"n_unmeasured"})


def _diffs(expected, actual, path="") -> list[str]:
    """NaN-aware 완전 동일 판정. 다른 곳을 전부 모읍니다.

    `ADDED_AFTER_PARITY` 에 적힌 **새 키**만 면제합니다 — 옛 픽스처에 없고 지금 출력에만
    있는 경우입니다. 반대 방향(옛 키가 사라진 것)과 값의 차이는 그대로 실패입니다.
    """
    out: list[str] = []
    if isinstance(expected, float) and isinstance(actual, float):
        if math.isnan(expected) and math.isnan(actual):
            return out
        if not math.isclose(expected, actual, rel_tol=1e-9, abs_tol=0.0):
            out.append(f"{path}: {expected!r} != {actual!r}")
        return out
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in sorted(set(expected) | set(actual)):
            if k not in expected and k in actual and k in ADDED_AFTER_PARITY:
                continue  # 뒤에 더한 키 — 위 목록 참고
            if k not in expected or k not in actual:
                out.append(f"{path}.{k}: 한쪽에만 있음 (old={k in expected}, new={k in actual})")
            else:
                out += _diffs(expected[k], actual[k], f"{path}.{k}")
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            out.append(f"{path}: 길이 {len(expected)} != {len(actual)}")
            return out
        for i, (x, y) in enumerate(zip(expected, actual)):
            out += _diffs(x, y, f"{path}[{i}]")
        return out
    if expected != actual:
        out.append(f"{path}: {expected!r} != {actual!r}")
    return out


def _cases():
    data = _load()
    return sorted(data["cases"])


@pytest.mark.parametrize("case", _cases())
def test_compare_v4_matches_old_gait_v4_compare_exactly(case: str) -> None:
    data = _load()
    entry = data["cases"][case]
    actual = compare_records(data["a"], entry["b"])
    diffs = _diffs(entry["expected"], actual, "compare")
    assert diffs == [], "\n".join(diffs[:20])


@pytest.mark.parametrize("case", _cases())
def test_only_the_listed_keys_were_added_after_parity(case: str) -> None:
    """면제 목록이 **실제로 늘어난 키와 정확히 같은지** 확인합니다.

    `_diffs` 가 면제해 주는 만큼, 무엇이 면제됐는지를 여기서 되짚습니다 — 면제 목록에
    이름을 적어 두고 정작 그 키가 안 나오면(또는 다른 자리에 나오면) 여기서 걸립니다.
    """
    data = _load()
    entry = data["cases"][case]
    actual = compare_records(data["a"], entry["b"])

    def added(expected, got) -> set[str]:
        out: set[str] = set()
        if isinstance(expected, dict) and isinstance(got, dict):
            out |= set(got) - set(expected)
            for k in set(expected) & set(got):
                out |= added(expected[k], got[k])
        return out

    assert added(entry["expected"], actual) <= ADDED_AFTER_PARITY


def test_fixture_covers_every_message_kind_and_the_unavailable_paths() -> None:
    """픽스처가 낡아 한 갈래만 남으면 parity 가 의미를 잃습니다 — 갈래 전부가 있어야 합니다."""
    data = _load()
    kinds = {c["expected"].get("message_kind") for c in data["cases"].values()}
    assert {"no_change", "one_side", "both_sides"} <= kinds
    statuses = {c["expected"]["status"] for c in data["cases"].values()}
    assert statuses == {"ok", "unavailable"}
    flagged = [c for c in data["cases"].values() if c["expected"].get("condition_flags")]
    assert flagged, "condition_flags 가 붙는 케이스가 없음"


def test_dev_only_keys_are_the_same_set_the_service_strips() -> None:
    """옛 구현과 같은 `_dev_only_*` 네 개를 내고, `_run_compare` 가 그것을 전부 걷어냅니다."""
    data = _load()
    b = data["cases"]["same_record"]["b"]
    raw = compare_records(data["a"], b)
    dev_keys = {k for k in raw if k.startswith("_dev_only_")}
    assert dev_keys == {
        "_dev_only_joint_notes_p90p10",
        "_dev_only_feature_versions",
        "_dev_only_raw_feature_cosine",
        "_dev_only_n_common_feature_dims",
    }
    routed = gait_service._run_compare(data["a"], b, pose_model="rtmpose_ap10k_ssd")
    assert not any(k.startswith("_dev_only_") for k in routed)
    assert set(raw) - dev_keys == set(routed)
