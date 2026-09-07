"""두 보행 기록 비교 — **판정만** 있는 모듈 (D-058).

⚠️ **여기에 무거운 import 를 넣지 마세요.** `numpy` 와 임계값 상수뿐입니다.
   `pipeline.py` 는 최상단에서 `keypoint_infer`(cv2)·`overlay` 를 끌고 오는데,
   backend 웹 컨테이너에는 `gait` 그룹(torch·cv2)이 없습니다. 비교를 거기 두면
   `/app/gait/compare` 가 **ImportError 로 500** 이 납니다 — 계산에 필요 없는
   의존성 때문에요. 그래서 판정을 이 파일로 떼어 냈습니다.

   판정 기준·임계값·문구는 옮기기 전과 **한 글자도 다르지 않습니다.**
"""

from __future__ import annotations

import numpy as np

from daengs_gait.config import COMPARE_DIFF_THRESHOLD


def compare_loaded_records(a: dict, b: dict) -> dict:
    """두 기록 비교 — **이미 불러온 기록**을 받습니다.

    파일에서 왔든 DB 에서 왔든 상관하지 않습니다 (D-058). backend 의 `/app/gait/compare`
    는 DB 행을 이 모양으로 맞춰 넘기고, 그래서 **비교가 저장소 구현과 무관**해집니다 —
    원본 영상도 overlay 도 읽지 않습니다.

    읽는 필드는 이것뿐입니다: `record_id` · `date` · `quality.status` ·
    `quality.quality_tier` · `quality.recommendation` · `features.summary_for_ui` ·
    `features.internal_feature_vector` · `gait_filter_version`.

    화면에는 **정성적 서술만** 내려갑니다 ("차이 관찰됨" / "비슷함"). 소수점 수치를 그대로
    노출하면 과신을 부른다고 판단한 결과입니다 — 표본이 작을 때 관절별 비율이 크게
    흩어지는 것을 실측으로 확인했습니다.

    `_dev_only_` 접두어가 붙은 값은 개발·검증용이고 **UI 에 절대 노출하면 안 됩니다.**
    """
    for r in (a, b):
        if r["quality"]["status"] != "ok":
            return {
                "status": "unavailable",
                "reason": f"비교 불가 — 기록 {r['record_id']}가 분석 가능 상태가 아닙니다.",
                "recommendation": r["quality"].get("recommendation"),
            }

    def _direction_note(va, vb):
        if va is None or vb is None:
            return "비교 불가(한쪽 기록에 없음)"
        denom = max(abs(va), abs(vb), 1e-9)
        rel_diff = abs(va - vb) / denom
        return "차이 관찰됨" if rel_diff > COMPARE_DIFF_THRESHOLD else "비슷함"

    joint_comparison = {}
    sa = a["features"]["summary_for_ui"]
    sb = b["features"]["summary_for_ui"]
    for joint in sorted(set(sa) | set(sb)):
        ja, jb = sa.get(joint), sb.get(joint)
        joint_comparison[joint] = {
            "record_a": ja,
            "record_b": jb,
            "comparison_note": {
                "x": _direction_note(
                    ja.get("x_range") if ja else None, jb.get("x_range") if jb else None
                ),
                "y": _direction_note(
                    ja.get("y_range") if ja else None, jb.get("y_range") if jb else None
                ),
            },
        }

    # 화면 문구는 **위에서 실제로 계산한 결과에서 유도합니다.** 고정 문자열로 두면 모든
    # 관절이 "비슷함" 이어도, 심지어 같은 기록끼리 비교해도 "차이가 관찰됩니다" 가
    # 나갑니다. 이 서비스는 진단이 아니라 같은 개체의 시간 변화 관찰이므로, 화면에 나가는
    # 주장은 계산 결과와 어긋나면 안 됩니다.
    #
    # "비교 가능한 관절이 하나도 없음" 을 "비슷함" 으로 뭉치지 않습니다 — 데이터가 없는
    # 것을 변화가 없다고 말하게 되기 때문입니다.
    _notes = [
        n for jc in joint_comparison.values() for n in jc["comparison_note"].values()
    ]
    _comparable = [n for n in _notes if n in ("차이 관찰됨", "비슷함")]
    if not _comparable:
        message_for_ui = (
            "두 기록에 공통으로 비교할 수 있는 관절 지표가 없어 변화를 말할 수 없습니다."
        )
    elif any(n == "차이 관찰됨" for n in _comparable):
        message_for_ui = "이전 기록과 비교해 일부 움직임 지표에서 차이가 관찰됩니다."
    else:
        message_for_ui = "이전 기록과 비교해 뚜렷한 차이는 관찰되지 않았습니다."

    low_tier_records = [
        r["record_id"] for r in (a, b) if r["quality"].get("quality_tier") != "good"
    ]
    reliability_note = (
        "두 기록 모두 유효 프레임 수가 적어 비교 결과는 참고용입니다."
        if len(low_tier_records) == 2
        else (
            f"기록 {low_tier_records[0]}은(는) 유효 프레임 수가 적어 "
            f"비교 결과가 참고용에 가깝습니다."
            if low_tier_records
            else None
        )
    )

    # 같은 영상이라도 필터 버전이 다르면 포함되는 프레임 구성이 달라져 이동범위가
    # 달라 보입니다 (실측 확인). 그것을 실제 걸음 차이로 읽으면 안 됩니다.
    ver_a = a.get("gait_filter_version")
    ver_b = b.get("gait_filter_version")
    if ver_a != ver_b:
        version_warning = (
            f"두 기록이 서로 다른 분석 버전으로 만들어졌습니다"
            f"(A: {ver_a or '기록 없음(구버전)'} / B: {ver_b or '기록 없음(구버전)'}) — "
            f"같은 영상이라도 버전이 다르면 어떤 프레임을 유효로 볼지 기준 자체가 달라져 "
            f"관절 이동범위 차이가 실제 걸음 차이가 아닐 수 있습니다."
        )
    else:
        version_warning = None

    va = a["features"]["internal_feature_vector"]
    vb = b["features"]["internal_feature_vector"]
    cols = sorted(set(va) & set(vb))
    xa = np.array([va[c] for c in cols], dtype=float)
    xb = np.array([vb[c] for c in cols], dtype=float)
    mask = ~(np.isnan(xa) | np.isnan(xb))
    xa, xb = xa[mask], xb[mask]
    raw_cosine = (
        float(np.sum(xa * xb) / (np.linalg.norm(xa) * np.linalg.norm(xb) + 1e-9))
        if len(xa) > 0
        else None
    )

    return {
        "status": "ok",
        "reason": None,
        "recommendation": None,
        "record_a": {"record_id": a["record_id"], "date": a["date"]},
        "record_b": {"record_id": b["record_id"], "date": b["date"]},
        "message_for_ui": message_for_ui,
        "reliability_note": reliability_note,
        "version_warning": version_warning,
        "diff_threshold_note": (
            f"'차이 관찰됨'은 두 값의 상대 차이가 {int(COMPARE_DIFF_THRESHOLD * 100)}% 이상일 때 "
            f"표시됩니다(검증된 임상 기준이 아니라 편의상 잡은 잠정 기준선입니다)."
        ),
        "joint_movement_range_comparison": joint_comparison,
        # ⚠️ 아래 둘은 UI 노출 금지. "보행 건강 점수"처럼 읽히면 안 됩니다.
        "_dev_only_raw_feature_cosine": raw_cosine,
        "_dev_only_n_common_feature_dims": int(mask.sum()),
    }
