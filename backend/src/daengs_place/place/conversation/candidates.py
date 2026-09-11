"""Candidate sets and explicit familiarity corrections, scoped to one exploration."""

import hashlib
import json
import re

from daengs_place.place.conversation.context import current_places, identity, unique_keys
from daengs_place.place.conversation.contract import NamedPlace
from daengs_place.place.conversation.grounding import compact, resolve_target
from daengs_place.place.conversation.intent import SemanticChanges

POOL_LABELS = {
    "all_places": "전체 장소",
    "bookmarks": "찜한 곳",
    "unbookmarked": "찜하지 않은 곳",
    "new_candidates": "새 후보",
}


def explicit_search(intent, query=None):
    if intent.feedback != "none":
        quote = intent.search_request_quote
        if (
            query is None
            or not quote
            or quote not in query
            or not re.search(r"찾|보여|볼래|보자|추천|필수|넓혀|바꿔|빼줘|해제", quote)
        ):
            return False
    return (
        intent.changes != SemanticChanges()
        or intent.search_scope != "keep"
        or intent.browse != "current"
        or intent.place_edit is not None
        or intent.refresh
    )


def grounded_feedback(intent, query):
    if intent.feedback == "none" or explicit_search(intent, query):
        return intent
    # Feedback alone cannot authorize model-invented filter or pool edits.
    return intent.model_copy(
        update={
            "changes": SemanticChanges(),
            "search_scope": "keep",
            "spatial_scope": "keep",
            "navigation": "stay",
            "refresh": False,
            "browse": "current",
            "place_edit": None,
            "region_query": "",
            "unsupported": (),
            "reference_index": None,
        }
    )


def correct_knowledge(request, intent):
    old = request.previous.exploration.known if request.previous else ()
    correction = intent.familiarity if intent else None
    if correction is None:
        return old, ()
    text = compact(correction.quote)
    if re.search(r"""["'“”‘’]|(?:라고|라는|라면|다면)""", request.query):
        raise ValueError("quoted or conditional correction")
    if correction.quote not in request.query or re.search(
        r"""[?"'“”‘’]|(?:모르|몰라|아니|않|못|라면|다면|라고|라는)|알(?:아|고).*싶""",
        text,
    ):
        raise ValueError("not a direct familiarity correction")
    if not re.search(r"이미(?:알|아는)|알고있|아는(?:곳|장소|데)|가봤|가본", text):
        raise ValueError("missing familiarity evidence")
    # The referent must belong to the same affirmative clause, not a later request.
    places = tuple(NamedPlace(key=p.key, name=p.name) for p in current_places(request))
    selected = request.visible_selected or request.previous.selected
    targets = tuple(
        p
        for target in correction.targets
        for p in resolve_target(correction.quote, target, places, selected)
    )
    previous = {identity(p.key): p for p in old}
    added = tuple({identity(p.key): p for p in targets if identity(p.key) not in previous}.values())
    if len(old) + len(added) > 120:
        raise ValueError("knowledge correction budget reached")
    return (*old, *added), added


def pool_omissions(pool, bookmarks, known, excluded):
    keys = tuple(p.key for p in excluded)
    if pool in {"unbookmarked", "new_candidates"}:
        if bookmarks is None:
            raise ValueError("bookmark set unavailable")
        keys = (*keys, *bookmarks)
    if pool == "new_candidates":
        keys = (*keys, *(p.key for p in known))
    return unique_keys(keys)


def pool_fingerprint(pool, bookmarks, known, excluded):
    keys = pool_omissions(pool, bookmarks, known, excluded)
    # K is part of the result basis even where it does not hide a place.
    value = [pool, sorted(identity(k) for k in keys), sorted(identity(p.key) for p in known)]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def presentation_fingerprint(filters_fingerprint, pool):
    # Keep old all-place sessions compatible without resetting their page history.
    return filters_fingerprint if pool == "all_places" else f"{filters_fingerprint}:{pool}"


def replace_known_hits(result, previous, omitted):
    """Keep unaffected cards while replenishing only vacancies in a current fresh page."""
    groups = []
    for group in result.groups:
        old = next((g for g in previous.groups if g.kind == group.kind), None)
        retained = tuple(h for h in old.matched if h.place.key not in omitted) if old else ()
        limit = result.applied_state.result_policy.limit_per_kind
        merged = (*retained, *group.matched)
        groups.append(
            group.model_copy(
                update={
                    "matched": merged[:limit],
                    "matched_truncated": group.matched_truncated or len(merged) > limit,
                }
            )
        )
    return result.model_copy(update={"groups": tuple(groups)})
