"""Saved search uses the existing bookmark filter/lookup capability, without member writes."""

from typing import Literal

from pydantic import Field, model_validator

from daengs_place.place.bookmarks import BookmarkFilters
from daengs_place.place.conversation.candidates import explicit_search, grounded_feedback
from daengs_place.place.conversation.scope import OUT_OF_SCOPE, validate_scope
from daengs_place.place.conversation.search_compilation import SearchAdapterError, compile_search
from daengs_place.place.conversation.search_policy import resolve_search
from daengs_place.place.filters.contract import FilterState
from daengs_place.place.planning.contract import PlanningModel


class SavedSearchRequest(PlanningModel):
    query: str = Field(min_length=1, max_length=1000)
    filters: BookmarkFilters
    search_policy: Literal["v1"] | None = None
    candidate_pools: Literal["v1"] | None = None


class SavedSearchPlan(PlanningModel):
    action: Literal["search", "clarify", "explain", "return_search", "search_places"]
    message: str
    filters: BookmarkFilters | None = None
    search_filters: FilterState | None = None
    search_pool: Literal["all_places", "unbookmarked", "new_candidates"] = "all_places"

    @model_validator(mode="after")
    def valid_payload(self):
        if (self.action == "search") != (self.filters is not None):
            raise ValueError("saved filters must match search action")
        if (self.action == "search_places") != (self.search_filters is not None):
            raise ValueError("ordinary filters must match search_places action")
        if self.action != "search_places" and self.search_pool != "all_places":
            raise ValueError("candidate pool requires a search action")
        return self


def plan_saved(current, intent, *, search_policy=None, query=None, candidate_pools=None):
    def clarify(message):
        return SavedSearchPlan(action="clarify", message=message)

    validate_scope(intent, query or "")
    if intent.kind == "out_of_scope":
        return SavedSearchPlan(action="explain", message=OUT_OF_SCOPE)
    if intent.kind == "facility_state" and intent.state_subject == "filters":
        return SavedSearchPlan(
            action="explain", message="지금 적용된 찜 조건은 조건 칩에서 볼 수 있어요."
        )
    intent = grounded_feedback(intent, query)
    directive = resolve_search(intent, "bookmarks", query, candidate_pools=candidate_pools)
    if directive.question:
        return clarify(directive.question)
    if directive.navigation:
        return SavedSearchPlan(action="return_search", message="이전 검색 결과로 돌아갈게요.")
    if intent.feedback != "none" and not explicit_search(intent, query):
        return SavedSearchPlan(
            action="explain",
            message=(
                "안내한 정보가 현장과 다를 수 있어요. 지금 자료만으로 이전이나 폐업 여부는 확인할 수 없어요."
                if intent.feedback == "information_dispute"
                else "원하는 업종이나 조건을 말해 주면 찜한 곳에서 찾아볼게요."
            ),
        )
    if intent.bookmark or intent.place_edit:
        return clarify("찜 해제는 장소의 하트를 눌러 주세요. 검색 조건 변경은 따로 말해 주세요.")
    if intent.unsupported:
        return clarify(
            "그 조건은 현재 자료로 확인할 수 없어요. 적용할 업종이나 주차 조건을 알려주세요."
        )
    if directive.pool != "bookmarks" and search_policy != "v1":
        return clarify("현재 찜 조건으로 일반 장소를 찾으려면 앱을 업데이트해 주세요.")
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
        filters = compile_search(current, intent, directive.pool)
    except SearchAdapterError as error:
        return clarify(str(error))
    except ValueError:
        return clarify(
            "함께 적용할 수 없는 조건이거나 기준 위치가 없어요. 업종·반경·필수 조건을 확인해 주세요."
        )
    if directive.pool != "bookmarks":
        return SavedSearchPlan(
            action="search_places", message="", search_filters=filters, search_pool=directive.pool
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
        plan = plan_saved(request.previous.filters, intent, query=request.query)
    except ValueError:
        plan = SavedSearchPlan(
            action="clarify",
            message="현재 조건 일부는 찜 검색으로 옮길 수 없어요. 찜 탭에서 조건을 확인해 주세요.",
        )
    if plan.filters and request.previous.exploration.excluded:
        if request.candidate_pools != "v1":
            plan = SavedSearchPlan(
                action="clarify",
                message="장소 제외를 유지하며 찜에서 찾으려면 앱을 업데이트해 주세요.",
            )
        else:
            plan = plan.model_copy(
                update={
                    "filters": plan.filters.model_copy(
                        update={
                            "excluded_keys": [p.key for p in request.previous.exploration.excluded],
                        }
                    )
                }
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
