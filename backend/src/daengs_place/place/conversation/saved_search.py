"""Saved search uses the existing bookmark filter/lookup capability, without member writes."""

from itertools import product
from typing import Literal

from pydantic import Field

from daengs_place.place.bookmarks import BookmarkFilters
from daengs_place.place.conversation.compiler import atom, compile_filter_data
from daengs_place.place.filters.evaluation import evaluate_atoms
from daengs_place.place.planning.contract import PlaceKind, PlanningModel


class SavedSearchRequest(PlanningModel):
    query: str = Field(min_length=1, max_length=1000)
    filters: BookmarkFilters


class SavedSearchPlan(PlanningModel):
    action: Literal["search", "clarify", "explain", "return_search"]
    message: str
    filters: BookmarkFilters | None = None


def from_search(state):
    kinds = list(state.candidate_kinds)
    if any(set(p.scope_kinds) != set(kinds) for p in state.preferences):
        raise ValueError("saved search cannot represent a scoped preference")
    return BookmarkFilters(
        **state.spatial.model_dump(),
        kinds=kinds,
        name_query=state.name_query,
        hard=state.hard,
        parking=bool(state.preferences),
        dogs=list(state.dogs),
    )


def compile_saved(current, changes, spatial_scope):
    kinds = list(current.kinds or PlaceKind)
    data = compile_filter_data(
        {
            "candidate_kinds": kinds,
            "spatial": {"radius_m": current.radius_m},
            "name_query": current.name_query,
            "hard": current.hard.model_dump(mode="json"),
            "preferences": [{**atom("operations.parking", True), "scope_kinds": kinds}]
            if current.parking
            else [],
            "dogs": [d.model_dump(mode="json") for d in current.dogs],
        },
        changes,
        max_kinds=len(PlaceKind),
    )
    if spatial_scope == "unbounded" and changes.radius_m is not None:
        raise ValueError("conflicting spatial scopes")
    candidate = BookmarkFilters.model_validate(
        {
            **current.model_dump(mode="json"),
            "kinds": []
            if set(data["candidate_kinds"]) == set(PlaceKind)
            else data["candidate_kinds"],
            "radius_m": None if spatial_scope == "unbounded" else data["spatial"]["radius_m"],
            "name_query": data["name_query"],
            "hard": data["hard"],
            "parking": bool(data["preferences"]),
        }
    )
    # Same three-valued fact semantics as ordinary search; contradictory OR branches
    # cannot silently disappear, and an unavailable fact never satisfies a requirement.
    for branch in candidate.hard.any or (None,):
        conjunction = (*candidate.hard.all, *(branch.all if branch else ()))
        worlds = [
            (kind, parking, exclusive)
            for kind, parking, exclusive in product(
                candidate.kinds or list(PlaceKind), (False, True), (False, True)
            )
            if evaluate_atoms(conjunction, kind, parking, exclusive) is True
        ]
        if not worlds or (candidate.parking and all(p is False for _, p, _ in worlds)):
            raise ValueError("contradictory saved filters")
    return candidate


def plan_saved(current, intent):
    def clarify(message):
        return SavedSearchPlan(action="clarify", message=message)

    if intent.feedback != "none":
        return SavedSearchPlan(
            action="explain",
            message=(
                "안내한 정보가 현장과 다를 수 있어요. 지금 자료만으로 이전이나 폐업 여부는 확인할 수 없어요."
                if intent.feedback == "information_dispute"
                else "원하는 업종이나 조건을 말해 주면 찜한 곳에서 찾아볼게요."
            ),
        )
    if intent.unresolved != "none" or intent.goal == "clarify":
        return clarify("찜 해제는 하트로 할 수 있어요. 검색에서 바꿀 조건을 구체적으로 알려주세요.")
    if intent.bookmark or intent.place_edit:
        return clarify("찜 해제는 장소의 하트를 눌러 주세요. 검색 조건 변경은 따로 말해 주세요.")
    if intent.region_query:
        return clarify(
            "다른 지역은 검색 탭의 지도에서 정한 뒤 ‘현재 검색 조건으로 찜 보기’를 눌러 주세요."
        )
    if intent.unsupported:
        return clarify(
            "그 조건은 현재 자료로 확인할 수 없어요. 적용할 업종이나 주차 조건을 알려주세요."
        )
    if intent.search_scope == "all_places":
        from daengs_place.place.conversation.intent import SemanticChanges

        if intent.changes != SemanticChanges() or intent.spatial_scope != "keep":
            return clarify(
                "찜한 곳 안에서 찾을까요, 일반 장소에서 찾을까요? 원하는 범위와 조건을 함께 말해 주세요."
            )
        return SavedSearchPlan(action="return_search", message="이전 검색 결과로 돌아갈게요.")
    if intent.goal not in {"show", "edit_only"} or intent.reference_index is not None:
        return clarify(
            "장소 정보는 해당 카드를 열어 확인해 주세요. 찜에서 찾을 조건을 말해 주세요."
        )
    if intent.browse != "current":
        return SavedSearchPlan(
            action="explain",
            message="찜 검색은 조건에 맞는 저장 장소 전체를 한 번에 보여줘요. 조건을 바꿔 더 찾아볼 수 있어요.",
        )
    try:
        filters = compile_saved(current, intent.changes, intent.spatial_scope)
    except ValueError:
        return clarify(
            "함께 적용할 수 없는 조건이거나 기준 위치가 없어요. 업종·반경·필수 조건을 확인해 주세요."
        )
    return SavedSearchPlan(action="search", message="", filters=filters)


def prepare_saved_search(request, intent, unchanged):
    if request.saved_search != "v1":
        return unchanged(
            request,
            "clarify",
            "saved_search_client_required",
            "찜 탭에서 조건을 골라 검색할 수 있어요. 말로 찾으려면 앱을 업데이트해 주세요.",
        )
    try:
        plan = plan_saved(from_search(request.previous.filters), intent)
    except ValueError:
        plan = SavedSearchPlan(
            action="clarify",
            message="현재 조건 일부는 찜 검색으로 옮길 수 없어요. 찜 탭에서 조건을 확인해 주세요.",
        )
    result = unchanged(
        request,
        "edit_only" if plan.action == "search" else "clarify",
        "saved_search_prepared" if plan.filters else "saved_search_clarify",
        plan.message,
    )
    return result.model_copy(
        update={
            "receipt": result.receipt.model_copy(
                update={
                    "saved_search_filters": plan.filters.model_dump(mode="json")
                    if plan.filters
                    else None,
                }
            )
        }
    )
