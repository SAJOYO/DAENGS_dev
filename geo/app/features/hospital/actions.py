"""병원 검색 응답에 붙는 결정론적 다음 행동.

v0는 LLM을 부르지 않는다. 결과 0곳의 복구와 불명확한 질문의 선택지만 코드로 조립한다.
전화는 후보마다 대상이 다르므로 검색 전체 액션이 아니라 결과 카드의 기본 행동으로 남긴다.
"""

from app.discovery.refine import tools
from app.discovery.refine.actions import Edit, SuggestedAction
from app.discovery.refine.labels import format_distance_m, value_label
from app.discovery.state import EditableState

MAX_ACTIONS = 3
MAX_RADIUS_M = 20_000


def build_actions(
    state: EditableState,
    *,
    result_count: int,
    question: str | None,
    dropped_by_hard_limit: int = 0,
) -> list[SuggestedAction]:
    """현재 응답에서 실제로 실행 가능하고 상태를 바꾸는 제안만 반환한다."""
    actions: list[SuggestedAction] = []

    if result_count == 0:
        if (dropped_by_hard_limit > 0 and state.journey.hard_limit
                and state.journey.max_total_min is not None):
            _append(
                actions, state, "show_over_time_limit", "시간 초과 병원도 보기",
                [Edit(tool="set_max_total_min", args={
                    "minutes": state.journey.max_total_min, "hard": False,
                })],
            )

        if state.target.radius_m < MAX_RADIUS_M:
            radius_m = min(state.target.radius_m * 2, MAX_RADIUS_M)
            _append(
                actions, state, "widen_radius", f"반경 {format_distance_m(radius_m)}로 넓히기",
                [Edit(tool="set_radius", args={"m": radius_m})],
            )

        if state.target.open_now:
            _append(
                actions, state, "relax_open_now", "지금 영업중 조건 풀기",
                [Edit(tool="set_open_now", args={"on": False})],
            )

        if state.target.require_tags:
            labels = [value_label(tag) for tag in state.target.require_tags]
            subject = " · ".join(labels) if len(labels) <= 2 else f"필수 조건 {len(labels)}개"
            _append(
                actions, state, "relax_required_tags", f"{subject} 풀기",
                [Edit(tool="unrequire", args={"tags": list(state.target.require_tags)})],
            )

    if question:
        if state.target.radius_m > 100:
            radius_m = max(state.target.radius_m // 2, 100)
            _append(
                actions, state, "narrow_radius", f"{format_distance_m(radius_m)} 안에서 보기",
                [Edit(tool="set_radius", args={"m": radius_m})],
            )
        if not state.target.night_service:
            _append(
                actions, state, "prefer_night_service", "야간 표방 병원 우선",
                [Edit(tool="set_night_service", args={"on": True})],
            )

    return actions[:MAX_ACTIONS]


def _append(
    actions: list[SuggestedAction],
    state: EditableState,
    action_id: str,
    label: str,
    edits: list[Edit],
) -> None:
    """등록 툴로 검증하고 no-op이면 제안하지 않는다."""
    if len(actions) >= MAX_ACTIONS:
        return
    after = state
    for edit in edits:
        after = tools.apply(after, edit.tool, edit.args)
    if after.snapshot() == state.snapshot():
        return
    actions.append(SuggestedAction(
        id=action_id, label=label, source="policy", edits=edits,
    ))
