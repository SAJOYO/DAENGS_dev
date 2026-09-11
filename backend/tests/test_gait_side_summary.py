"""`side_summary` — 다리별 변화 판정의 **정본** (D-063 7단계).

이 판정은 지금까지 서버와 앱 **양쪽에** 있었습니다. 서버 `compare_v4.side_summary[*].flagged`
(`n_diff >= SIDE_MIN_DIFF`)와 앱 `verdictOf()`(다리별 `changed >= CHANGED_JOINTS_FOR_LEG`)가
그것이고, **두 임계값이 우연히 같아서**(둘 다 2) 결과가 일치해 왔습니다. 한쪽만 바뀌면
조용히 갈라지는 자리라 7단계에서 서버 하나로 모읍니다.

앱이 서버 판정을 쓰려면 **"못 잰 관절"의 수**가 필요합니다. 앱에는 서버에 없는 갈래가 하나
있기 때문입니다:

    NotEnough — 비교할 관절을 충분히 못 쟀다

이 갈래가 무너지면 **못 잰 것이 "뚜렷한 차이 없음"으로 흘러들어 없는 안심을 줍니다.**
그래서 `n_unmeasured` 를 더합니다. 이 파일은 그 수를 **무엇으로 세는지**를 못 박습니다.

## 세는 기준은 문자열이 아니라 값의 유무입니다

`direction_note` 는 `va is None or vb is None` 일 때만 `"비교 불가(한쪽 기록에 없음)"` 를
냅니다. 그 문자열을 비교해서 세면 **문구를 다듬는 순간 집계가 조용히 틀립니다.** 값의
유무로 셉니다 — 아래 `test_axis_unmeasured_is_value_presence_not_wording` 가 그 둘이 같은
집합임을 보입니다.

## 앱의 `Unknown` 조건과 같아야 합니다

앱 `GaitJointChange.of(x, y)`(`GaitJoints.kt`)는 이렇습니다:

    x==Changed && y==Changed -> Both
    x==Changed               -> Horizontal
    y==Changed               -> Vertical
    x==Similar && y==Similar -> None
    else                     -> Unknown      ← "못 잰 관절"

즉 **어느 축도 `차이 관찰됨` 이 아니고, 적어도 한 축을 못 쟀을 때**가 Unknown 입니다.
아래 진리표 테스트가 서버 집계를 그 정의에 묶어 둡니다. 여기를 고치려면 앱을 같이
고쳐야 합니다 — 한쪽만 바꾸면 7단계 이전으로 돌아갑니다.
"""

from __future__ import annotations

import pytest

from daengs_gait.compare import direction_note
from daengs_gait.compare_v4 import SIDE_MIN_DIFF, compare_records

UNCOMPARABLE = "비교 불가(한쪽 기록에 없음)"


def _record(rid: str, joints: dict, *, tier: str = "good", version: str = "v1") -> dict:
    """`compare_records` 가 읽는 필드만 채운 v4 기록."""
    return {
        "record_id": rid,
        "date": "2026-09-11",
        "pose_model": "rtmpose_ap10k_ssd",
        "quality": {"status": "ok", "quality_tier": tier},
        "gait_filter_version": version,
        "features": {
            "summary_for_ui": joints,
            "internal_feature_vector": {"f0": 1.0},
        },
    }


def _joint(x: float | None, y: float | None) -> dict:
    """관절 하나. `None` 은 그 축을 **못 쟀다**는 뜻입니다(키 자체가 없는 것과 같게 다룹니다)."""
    out = {}
    if x is not None:
        out["x_range"] = x
    if y is not None:
        out["y_range"] = y
    return out


# ── 축 하나: 무엇이 "못 쟴"인가 ───────────────────────────────────────────────
@pytest.mark.parametrize(
    ("va", "vb", "expected"),
    [
        (10.0, 10.0, "비슷함"),
        (10.0, 1000.0, "차이 관찰됨"),
        (10.0, None, UNCOMPARABLE),
        (None, 10.0, UNCOMPARABLE),
        (None, None, UNCOMPARABLE),
    ],
)
def test_axis_unmeasured_is_value_presence_not_wording(va, vb, expected) -> None:
    """`direction_note` 의 세 갈래와 **값 유무**가 정확히 대응합니다.

    집계는 이 문자열이 아니라 `va is None or vb is None` 으로 합니다 — 문구가 바뀌어도
    집계가 안 흔들리게 하려는 것이고, 이 테스트가 그 둘이 같은 집합임을 보입니다.
    """
    note = direction_note(va, vb)
    assert note == expected
    assert (note == UNCOMPARABLE) == (va is None or vb is None)


# ── 관절 하나: 앱 `GaitJointChange.of` 진리표 (9칸) ───────────────────────────
#: (x 축 상태, y 축 상태) → 이 관절을 "못 쟨 것"으로 세는가.
#: 앱 `GaitJointChange.of` 가 `Unknown` 을 내는 칸과 같아야 합니다.
_AXIS_VALUES = {
    "Similar": (10.0, 10.0),      # 둘 다 값 있음, 차이 없음
    "Changed": (10.0, 1000.0),    # 둘 다 값 있음, 차이 있음
    "Unknown": (10.0, None),      # 한쪽에 값이 없음
}
_TRUTH_TABLE = {
    ("Changed", "Changed"): False,   # Both
    ("Changed", "Similar"): False,   # Horizontal
    ("Changed", "Unknown"): False,   # Horizontal — 잡힌 변화를 못 잰 축 때문에 감추지 않는다
    ("Similar", "Changed"): False,   # Vertical
    ("Unknown", "Changed"): False,   # Vertical
    ("Similar", "Similar"): False,   # None
    ("Similar", "Unknown"): True,    # Unknown — 못 잰 것을 "비슷함"으로 올리지 않는다
    ("Unknown", "Similar"): True,    # Unknown
    ("Unknown", "Unknown"): True,    # Unknown
}


@pytest.mark.parametrize(("state", "unmeasured"), sorted(_TRUTH_TABLE.items()))
def test_joint_unmeasured_truth_table_matches_the_app(state, unmeasured) -> None:
    """앱 `GaitJointChange.of` 가 `Unknown` 을 내는 칸에서만 `n_unmeasured` 가 올라갑니다.

    ⚠️ 이 표를 고치면 앱 `GaitJoints.kt` 의 같은 규칙도 같이 고쳐야 합니다. 한쪽만 바꾸면
       "못 잼"의 뜻이 서버와 앱에서 갈라집니다.
    """
    xs, ys = state
    xa, xb = _AXIS_VALUES[xs]
    ya, yb = _AXIS_VALUES[ys]

    a = _record("aaaaaaaa", {"L_Hip": _joint(xa, ya)})
    b = _record("bbbbbbbb", {"L_Hip": _joint(xb, yb)})

    side = compare_records(a, b)["side_summary"]["왼쪽"]

    assert side["n_joints"] == 1
    assert side["n_unmeasured"] == (1 if unmeasured else 0)


# ── 다리별 집계 ──────────────────────────────────────────────────────────────
def test_side_summary_counts_joints_diff_and_unmeasured_per_leg() -> None:
    """한 다리 안에서 세 수가 각자 제 몫을 셉니다 — 합이 아니라 **분류**입니다."""
    a = _record(
        "aaaaaaaa",
        {
            "L_Hip": _joint(10.0, 10.0),      # 비슷함
            "L_Knee": _joint(10.0, 10.0),     # 차이 (b 에서 키움)
            "L_B_Paw": _joint(10.0, None),    # 못 쟴 (y 없음)
            "R_Hip": _joint(10.0, 10.0),
        },
    )
    b = _record(
        "bbbbbbbb",
        {
            "L_Hip": _joint(10.0, 10.0),
            "L_Knee": _joint(1000.0, 10.0),
            "L_B_Paw": _joint(10.0, None),
            "R_Hip": _joint(10.0, 10.0),
        },
    )

    sides = compare_records(a, b)["side_summary"]

    assert sides["왼쪽"] == {
        "n_joints": 3,
        "n_diff": 1,
        "diff_joints": ["L_Knee"],
        "n_unmeasured": 1,
        "flagged": False,
    }
    assert sides["오른쪽"]["n_joints"] == 1
    assert sides["오른쪽"]["n_unmeasured"] == 0


def test_unmeasured_does_not_change_flagged_threshold() -> None:
    """`n_unmeasured` 는 **세기만** 합니다 — `flagged` 규칙은 그대로 `n_diff >= 2` 입니다."""
    joints_a = {"L_Hip": _joint(10.0, 10.0), "L_Knee": _joint(10.0, 10.0), "L_B_Paw": _joint(10.0, None)}
    joints_b = {"L_Hip": _joint(1000.0, 10.0), "L_Knee": _joint(1000.0, 10.0), "L_B_Paw": _joint(10.0, None)}

    side = compare_records(_record("aaaaaaaa", joints_a), _record("bbbbbbbb", joints_b))["side_summary"]["왼쪽"]

    assert SIDE_MIN_DIFF == 2
    assert side["n_diff"] == 2 and side["flagged"] is True
    assert side["n_unmeasured"] == 1  # 못 잰 관절이 있어도 잡힌 변화는 그대로 말한다


def test_joint_missing_from_both_records_is_not_counted_at_all() -> None:
    """두 기록 **모두에 없는** 관절은 `n_joints` 에도 안 들어갑니다.

    앱은 고정 6관절을 그리므로 그 관절이 화면에서는 "측정 부족" 한 줄이 됩니다. 세는
    기준이 다르다는 뜻이라, 앱은 `n_joints` 를 그대로 믿지 말고 `n_joints - n_unmeasured`
    (= 실제로 잰 수)만 써야 합니다.
    """
    a = _record("aaaaaaaa", {"L_Hip": _joint(10.0, 10.0)})
    b = _record("bbbbbbbb", {"L_Hip": _joint(10.0, 10.0)})

    side = compare_records(a, b)["side_summary"]["왼쪽"]

    assert side["n_joints"] == 1  # L_Knee · L_B_Paw 는 어디에도 없으므로 안 셈
    assert side["n_unmeasured"] == 0


def test_legacy_joint_names_fall_into_the_whole_bucket() -> None:
    """legacy 관절 이름은 좌/우로 안 갈려 `전체` 한 덩어리입니다.

    v4 비교에서는 나올 일이 없지만(관절 이름이 전부 `L_`/`R_`), 이 모양이 앱에 가면
    다리별 판정에 쓸 수 없습니다 — 앱이 fallback 으로 떨어져야 하는 조건입니다.
    """
    joints = {"Iliac crest": _joint(10.0, 10.0), "Femorotibial joint": _joint(10.0, 10.0)}

    sides = compare_records(_record("aaaaaaaa", joints), _record("bbbbbbbb", joints))["side_summary"]

    assert set(sides) == {"전체"}
    assert sides["전체"]["n_joints"] == 2


# ── 외부 계약 ────────────────────────────────────────────────────────────────
def test_response_model_carries_side_summary_for_v4() -> None:
    """v4 비교의 `side_summary` 가 **응답 모델을 통과해** 앱까지 갑니다 (D-063 7단계)."""
    import json

    from daengs_backend.schemas.gait import GaitCompareResponse

    joints = {f"{side}_{name}": _joint(10.0, 10.0) for side in ("L", "R") for name in ("Hip", "Knee", "B_Paw")}
    out = compare_records(_record("aaaaaaaa", joints), _record("bbbbbbbb", joints))
    public = {k: v for k, v in out.items() if not k.startswith("_dev_only_")}

    dumped = json.loads(GaitCompareResponse(**public).model_dump_json())

    assert set(dumped["side_summary"]) == {"왼쪽", "오른쪽"}
    assert dumped["side_summary"]["왼쪽"]["n_joints"] == 3
    assert dumped["side_summary"]["왼쪽"]["n_unmeasured"] == 0
    assert dumped["side_summary"]["왼쪽"]["flagged"] is False
    assert [k for k in dumped if k.startswith("_dev_only_")] == []


def test_message_kind_and_condition_flags_stay_out_of_the_contract() -> None:
    """7단계 범위는 `side_summary` 하나입니다.

    `message_kind` 는 앱의 "관절을 충분히 못 쟴" 갈래를 표현하지 못해 그대로 공개하면
    **못 잰 것을 '차이 없음' 으로 말하게 됩니다.** `condition_flags` 는 production 에서
    일부 경로가 값을 못 받아 살아 있지 않습니다. 둘 다 이번 계약에 넣지 않습니다 —
    계산은 그대로 두고 계약에서만 뺍니다(응답 모델이 걸러 냅니다).
    """
    from daengs_backend.schemas.gait import GaitCompareResponse

    joints = {"L_Hip": _joint(10.0, 10.0)}
    out = compare_records(_record("aaaaaaaa", joints), _record("bbbbbbbb", joints))
    assert "message_kind" in out and "condition_flags" in out  # 계산은 그대로 있다

    fields = set(GaitCompareResponse.model_fields)
    assert "message_kind" not in fields
    assert "condition_flags" not in fields


def test_legacy_compare_sends_side_summary_as_null() -> None:
    """legacy 비교에는 다리별 판정이 **없습니다** — 키는 실리되 값이 `null` 입니다.

    이것은 「계약 불변」이 아니라 **backward-compatible additive change** 입니다:
    응답 JSON 에 키가 하나 늘어납니다(`response_model_exclude_none` 을 안 쓰므로 `None` 도
    실립니다). 기존 키·값은 그대로이고, 앱 파서는 모르는 키를 무시하므로 옛 빌드도 안
    깨집니다. 받는 쪽은 `null` 이면 자기 판정으로 떨어져야 합니다.
    """
    import json

    from daengs_backend.schemas.gait import GaitCompareResponse
    from daengs_gait.compare import compare_loaded_records

    joints = {"Iliac crest": {"x_range": 10.0, "y_range": 10.0}}
    a = _record("aaaaaaaa", joints)
    b = _record("bbbbbbbb", joints)
    out = compare_loaded_records(a, b)
    assert "side_summary" not in out  # legacy 비교 함수는 애초에 안 만든다

    public = {k: v for k, v in out.items() if not k.startswith("_dev_only_")}
    dumped = json.loads(GaitCompareResponse(**public).model_dump_json())

    assert "side_summary" in dumped and dumped["side_summary"] is None
