"""Facility authority checks. Persona wording is server-owned, never model prose."""

import re

from daengs_place.place.conversation.intent import ScopedInterpretation

OUT_OF_SCOPE = "멍, 그건 잘 몰라요. 장소 찾는 건 맡겨줘요 🐾"
PROCESSING_FAILED = "앗, 요청을 처리하지 못했어요. 다시 시도해 주세요 🐾"
PRESERVE_CODES = frozenset(
    {"facility_out_of_scope", "invalid_plan", "facility_scope_unclear", "facility_filters"}
)

# Used only to reject a second facility command alongside a persistent bookmark write.
KIND_WORDS = {
    "hospital": r"병원|진료",
    "pharmacy": r"약국|의약품",
    "pet_shop": r"용품|펫샵|펫숍|쇼핑",
    "shopping": r"쇼핑|마트|백화점",
    "grooming": r"미용|돌봄",
    "boarding": r"위탁|돌봄|돌보|맡길",
    "travel": r"여행|나들이|놀러",
    "leisure": r"레저|나들이|놀러",
    "museum": r"박물관|문화",
    "gallery": r"미술관|갤러리|문화",
    "arts_center": r"문예|공연|문화",
    "culture": r"문화",
    "cafe": r"카페|커피",
    "restaurant": r"음식점|식당|밥|식사",
    "pension": r"펜션|숙박",
    "hotel": r"호텔|숙박",
    "stay": r"숙박|숙소|묵을",
    "etc": r"기타",
}
FACILITY_WORDS = (
    "|".join(KIND_WORDS.values())
    + r"|시설|장소|주차|반경|전용|동반|찜|저장|조건|후보|목록|여기|거기|이곳|그곳|첫\s*번째|두\s*번째|\d+\s*번|새로운\s*곳|다른\s*곳|아무\s*데나|한\s*곳|하나\s*골라"
)


def validate_scope(intent, query, previous=None):
    """Read/search intent belongs to the model; validate contracts, not vocabulary."""
    if intent.kind is None:
        return  # Internal deterministic callers; the provider requires ScopedInterpretation.
    ScopedInterpretation.model_validate(intent.model_dump())
    if intent.bookmark is None:
        return
    # Persistent writes retain their explicit command boundary.
    if not intent.request_quote or intent.request_quote not in query:
        raise ValueError("bookmark evidence absent from current request")
    changes = intent.changes
    # Reject reported/quoted/hypothetical commands. Literal quoted place names are data.
    text = query
    names = [changes.name_query] if changes.name_query else []
    if intent.bookmark and intent.bookmark.target.kind == "name":
        names.append(intent.bookmark.target.text)
    for name in names:
        for left, right in [("'", "'"), ('"', '"'), ("‘", "’"), ("“", "”")]:
            text = text.replace(left + name + right, "상호")
    if re.search(
        r"""["'“”‘’]|라고\s*(?:했|하|말)|라면|다면|면\s*(?:어떻게|뭐)|말라는|하지\s*마""", text
    ):
        raise ValueError("quoted or hypothetical commands have no authority")
    if re.search(
        r"(?:찾|보여|추천|골라|선택|적용|변경|찜|저장|제외)[가-힣 ]*(?:하지\s*말|하지\s*마|지\s*말|지\s*마)",
        text,
    ):
        raise ValueError("negated command has no authority")


def bookmark_clause(query, intent):
    """Allow an isolated positive command plus unrelated prose, never a second facility request."""
    if intent.kind is None:
        return query
    quote = intent.request_quote.strip().rstrip(".!~ ")
    # A period in a literal place name is not a sentence boundary.
    target = intent.bookmark.target
    marked = query.replace(target.text, "\x00place\x00") if target.kind == "name" else query
    clauses = [
        part.strip().replace("\x00place\x00", target.text)
        for part in re.split(r"[.!?。\n]+", marked)
        if part.strip()
    ]
    if intent.request_quote == query and len(clauses) > 1:
        # Some models include the entire utterance as evidence. Extract only a unique,
        # standalone target clause; the existing whole-command grammar still validates it.
        candidates = [part for part in clauses if intent.bookmark.target.text in part]
        if len(candidates) == 1:
            quote = candidates[0]
    if quote not in clauses:
        raise ValueError("bookmark evidence must be a complete standalone clause")
    others = [part for part in clauses if part != quote]
    if any(re.search(FACILITY_WORDS + r"|말고|취소|하지|안\s*해|하지마", part) for part in others):
        raise ValueError("another clause changes facility authority")
    return quote
