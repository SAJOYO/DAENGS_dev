"""상태 diff → 사람이 읽는 changes. **정책별로 묶어서** 낸다.

서버가 만든다. LLM이 "좁혔어요"라 말해놓고 실제론 안 바뀐 상황을 막는 장치.
target 변경은 "결과가 바뀐다", journey 변경은 "같은 결과, 가는 방법이 바뀐다" — 사용자에게 다르게 읽혀야 한다.
"""

from app.discovery.refine.labels import format_distance_m, value_label
from app.discovery.state import EditableState

POLICY_LABEL = {"context": "상황", "target": "찾는 곳", "journey": "가는 길", "view": "보기"}
_TIME_KIND = {"depart_at": "출발", "arrive_by": "도착 기한", "service_at": "진료 시각"}


def changes_by_policy(before: EditableState | None, after: EditableState) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"context": [], "target": [], "journey": [], "view": []}
    if before is None:
        out["target"].append(f"초안: 반경 {format_distance_m(after.target.radius_m)}, 필터 없음")
        return out

    bt, at_ = before.target, after.target
    bj, aj = before.journey, after.journey

    # ---- context: 사실. 결과를 거르지도, 경로를 고르지도 않는다 — 둘 다의 입력일 뿐
    if before.urgency != after.urgency:
        out["context"].append("급한 상황" if after.urgency == "urgent" else "긴급 해제")
    if before.time_intent != after.time_intent:
        ti = after.time_intent
        out["context"].append(
            "시각 기준 해제" if ti is None
            else f"{_TIME_KIND[ti.kind]} {ti.at:%m/%d %H:%M}")

    # ---- target: 결과 집합이 바뀐다
    if (before.lat, before.lng) != (after.lat, after.lng):
        out["target"].append("기준 위치 변경")
    if bt.radius_m != at_.radius_m:
        out["target"].append(
            f"반경 {format_distance_m(bt.radius_m)} → {format_distance_m(at_.radius_m)}")
    # 라벨은 필드가 실제로 하는 일을 말한다. 야간·응급은 **거르지 않고 위로 올릴 뿐**이다
    # (geo/search.py `_prefer_tags`) — "야간만"이라고 쓰면 사용자는 나머지가 사라졌다고 읽는다.
    for f, on, off in (("open_now", "지금 영업중만", "영업중 필터 해제"),
                       ("night_service", "야간 표방 우선", "야간 우선 해제"),
                       ("emergency_service", "응급 표방 우선", "응급 우선 해제")):
        if getattr(bt, f) != getattr(at_, f):
            out["target"].append(on if getattr(at_, f) else off)
    if bt.symptoms != at_.symptoms:
        out["target"].append("증상 메모: " + (", ".join(at_.symptoms) or "없음"))
    if bt.require_tags != at_.require_tags:
        out["target"].append("필수: " + (", ".join(value_label(t) for t in at_.require_tags) or "없음"))
    added = set(at_.exclude_ids) - set(bt.exclude_ids)
    if added:
        out["target"].append(f"{len(added)}곳 제외")
    if set(at_.pin_ids) - set(bt.pin_ids):
        out["target"].append("고정 추가")

    # ---- journey: 결과는 그대로, 가는 방법만
    # -- 수단 무관 (scope: any)
    if bj.preferred_mode != aj.preferred_mode:
        out["journey"].append(
            f"이동수단: {value_label(aj.preferred_mode) if aj.preferred_mode else '미지정'}")
    if (bj.max_total_min, bj.hard_limit) != (aj.max_total_min, aj.hard_limit):
        if aj.max_total_min is None:
            out["journey"].append("전체 시간 제한 해제")
        else:
            out["journey"].append(f"전체 {aj.max_total_min}분 이내"
                                  + (" (초과 제외)" if aj.hard_limit else " (표시만)"))

    # -- 도보로 갈 때만 (scope: walk). 차·대중교통을 고른 상태면 '도보 대안'으로 내려서 말한다.
    #    숨기지는 않는다 — 사용자가 바꿨는데 아무 반응이 없으면 그게 더 나쁘다. 상태에도 남는다.
    pre = "" if aj.preferred_mode in (None, "walk") else "도보 대안 — "
    if bj.walk.max_walk_min != aj.walk.max_walk_min:
        out["journey"].append(f"{pre}도보 시간 제한 해제" if aj.walk.max_walk_min is None
                              else f"{pre}도보 {aj.walk.max_walk_min}분 이내")

    # ---- view
    if before.sort != after.sort:
        out["view"].append(f"정렬: {value_label(after.sort)}")
    return out


def changes(before: EditableState | None, after: EditableState) -> list[str]:
    g = changes_by_policy(before, after)
    flat = g["context"] + g["target"] + g["journey"] + g["view"]
    return flat or ["변경 없음"]
