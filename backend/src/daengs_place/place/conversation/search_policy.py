"""Shared request meaning and search compilation; storage and UI remain adapters."""

from dataclasses import dataclass

from daengs_place.place.conversation.intent import Interpretation, SearchPool, SemanticChanges


@dataclass(frozen=True)
class SearchDirective:
    pool: SearchPool
    navigation: bool = False
    question: str = ""
    code: str = ""


def resolve_search(
    intent: Interpretation, current: SearchPool, query: str | None = None, *, candidate_pools=None
) -> SearchDirective:
    """No mention means retain the active pool, never infer it from a write."""
    requested = intent.search_scope

    if requested in {"unbookmarked", "new_candidates"} and candidate_pools != "v1":
        return SearchDirective(
            current,
            question="찜한 곳을 제외하거나 새 후보만 찾는 기능은 아직 준비 중이에요. 현재 조건과 결과를 유지할게요.",
            code="candidate_pool_unavailable",
        )
    pool = current if requested == "keep" else requested
    if intent.forbid_save and intent.bookmark and intent.bookmark.operation == "save":
        return SearchDirective(
            current,
            question="저장하지 않겠다는 요청과 찜 저장 요청이 함께 있어요. 원하는 동작을 알려주세요.",
            code="save_forbidden",
        )
    if intent.navigation == "restore_search":
        if (
            intent.search_scope != "keep"
            or intent.changes != SemanticChanges()
            or intent.spatial_scope != "keep"
            or intent.bookmark
            or intent.place_edit
            or intent.familiarity
            or intent.browse != "current"
            or intent.refresh
            or intent.region_query
            or intent.unsupported
            or intent.unresolved != "none"
        ):
            return SearchDirective(
                current,
                question="이전 화면으로 돌아갈지, 현재 조건으로 새로 검색할지 알려주세요.",
                code="navigation_with_changes",
            )
        return SearchDirective(current, navigation=True)
    if intent.unresolved != "none" or intent.goal == "clarify":
        return SearchDirective(
            current,
            question="바꾸려는 장소나 검색 조건을 구체적으로 알려주세요.",
            code="clarification_required",
        )
    if intent.region_query:
        return SearchDirective(
            current,
            question="검색 지역은 지도에서 선택해 주세요. 다른 조건은 그대로 유지할게요.",
            code="region_change_unsupported",
        )
    if intent.bookmark and (pool != current or intent.spatial_scope != "keep"):
        return SearchDirective(
            current,
            question="검색 범위 변경과 찜 저장·해제는 따로 요청해 주세요.",
            code="bookmark_with_scope",
        )
    if intent.spatial_scope == "unbounded" and pool != "bookmarks":
        return SearchDirective(
            current,
            question="일반 장소 검색에는 기준 위치와 반경이 필요해요.",
            code="unbounded_requires_saved",
        )
    return SearchDirective(pool)
