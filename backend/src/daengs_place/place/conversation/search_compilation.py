"""Common condition calculation with lossless adapters for the two existing lookups."""

from itertools import product

from daengs_place.place.bookmarks import BookmarkFilters
from daengs_place.place.conversation.compiler import atom, compile_changes, compile_filter_data
from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.evaluation import evaluate_atoms
from daengs_place.place.planning.contract import PlaceKind


class SearchAdapterError(ValueError):
    """An explicit supported-range failure, safe to explain without losing conditions."""


def from_search(state):
    if state.unknown_policy != "exclude":
        raise SearchAdapterError(
            "찜 조회는 정보 미확인 결과를 따로 표시하는 조건을 지원하지 않아요."
        )
    kinds = list(state.candidate_kinds)
    if any(set(p.scope_kinds) != set(kinds) for p in state.preferences):
        raise SearchAdapterError(
            "업종별로 다른 주차 선호는 찜 조회로 옮길 수 없어요. 주차 선호 범위를 먼저 정해 주세요."
        )
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


def to_search(current: BookmarkFilters) -> FilterState:
    # No invented origin, radius, category truncation or lost hard/OR conditions.
    kinds = tuple(current.kinds or PlaceKind)
    if current.lat is None or current.radius_m is None:
        raise SearchAdapterError(
            "일반 장소 검색에는 기준 위치와 반경이 필요해요. 먼저 지도 위치와 반경을 정해 주세요."
        )
    if len(kinds) > 6:
        raise SearchAdapterError(
            "일반 장소 검색은 한 번에 6개 업종까지 가능해요. 찾을 업종을 골라 주세요."
        )
    return FilterState(
        candidate_kinds=kinds,
        spatial={"lat": current.lat, "lng": current.lng, "radius_m": current.radius_m},
        name_query=current.name_query,
        hard=current.hard,
        preferences=({**atom("operations.parking", True), "scope_kinds": kinds},)
        if current.parking
        else (),
        dogs=tuple(current.dogs),
    )


def compile_search(current, intent, pool):
    """Apply edits before adapting: a request may clear an unrepresentable condition."""
    if isinstance(current, BookmarkFilters):
        candidate = compile_saved(current, intent.changes, intent.spatial_scope)
        return candidate if pool == "bookmarks" else to_search(candidate)
    candidate = compile_changes(current, intent.changes)
    if pool == "all_places":
        return candidate
    saved = from_search(candidate)
    if intent.spatial_scope == "unbounded":
        if intent.changes.radius_m is not None:
            raise ValueError("conflicting spatial scopes")
        saved = saved.model_copy(update={"radius_m": None})
    return saved
