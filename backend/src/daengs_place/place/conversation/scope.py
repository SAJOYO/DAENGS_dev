"""Facility authority checks. Persona wording is server-owned, never model prose."""

import re

from daengs_place.place.conversation.intent import ScopedInterpretation

OUT_OF_SCOPE = "멍, 그건 잘 몰라요. 장소 찾는 건 맡겨줘요 🐾"
INVALID_REQUEST = "원하는 장소나 바꿀 조건을 짧게 알려주세요."
PRESERVE_CODES = frozenset(
    {"facility_out_of_scope", "invalid_plan", "facility_scope_unclear", "facility_filters"}
)

# These are evidence for supported capabilities, not a general topic/ban-word classifier.
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
FOLLOWUP = r"(?:더|다시|다음|다른\s*곳|가까운\s*곳|주변|한\s*곳|하나|아무\s*데나)?\s*(?:보여줘|보여주세요|찾아줘|찾아주세요|골라줘|골라주세요|추천해줘|처음부터|새로고침)[.!~ ]*"


class OutsideFacilityScope(ValueError):
    """A definition request has no current-facility state to read."""


def validate_scope(intent, query, previous=None):
    """Live proposals need literal facility evidence before compilation or any operation."""
    if intent.kind is None:  # Existing deterministic planners/research have no provider authority.
        return
    ScopedInterpretation.model_validate(intent.model_dump())
    if intent.kind == "out_of_scope":
        return
    quote = intent.request_quote
    if quote not in query:
        raise ValueError("facility evidence is absent from the current utterance")
    if intent.kind == "facility_state" and re.search(
        r"(?:이?란|의\s*뜻|의\s*정의)\s*(?:게|것은|뭐|무엇|알려|설명)", quote
    ):
        raise OutsideFacilityScope("facility definitions are not state queries")
    undo_kinds = set()
    if (
        previous
        and previous.history
        and intent.changes.kinds
        and intent.changes.kinds.operation == "remove"
        and re.fullmatch(r"(?:방금|아까)\s*추가한\s*(?:것|거)만?\s*취소해줘[.!~ ]*", quote)
    ):
        last = previous.history[-1]
        if last.goal in {"show", "edit_only"} and re.search(r"도|추가", last.query):
            undo_kinds = {
                kind
                for kind in previous.filters.candidate_kinds
                if re.search(KIND_WORDS[kind], last.query)
            }
    if not (re.search(FACILITY_WORDS, quote) or re.fullmatch(FOLLOWUP, quote) or undo_kinds):
        raise ValueError("no supported facility request evidence")
    if intent.kind != "facility_action":
        return
    changes = intent.changes
    if intent.goal == "pick_one" and not re.search(r"골라|선택|하나|한\s*곳|아무\s*데나", quote):
        raise ValueError("selection lacks a current request")
    if (
        intent.goal == "show"
        and not changes.model_dump(exclude_defaults=True)
        and not (intent.bookmark or intent.place_edit or intent.familiarity)
        and intent.search_scope == "keep"
        and intent.navigation == "stay"
        and not re.search(r"찾|보여|추천|다음|다른|다시|새로|처음부터|갖고와|가져", quote)
    ):
        raise ValueError("search lacks a current request")
    for kind in changes.kinds.values if changes.kinds else ():
        if not re.search(KIND_WORDS[kind], quote) and kind not in undo_kinds:
            raise ValueError("category change lacks evidence")
    for changed, pattern in [
        (changes.parking != "keep", r"주차"),
        (changes.exclusive != "keep", r"전용"),
        (changes.radius_m is not None, r"반경|거리|[0-9]\s*(?:km|m|킬로|미터)"),
        (changes.alternatives is not None, r"주차|전용|조건|거나|또는"),
    ]:
        if changed and not re.search(pattern, quote, re.IGNORECASE):
            raise ValueError("filter change lacks current-request evidence")
    if changes.name_query and changes.name_query not in quote:
        raise ValueError("place name was invented")
    named = re.search(r"(\S+?)(?:이?라는)\s*(?:카페|식당|음식점|호텔|펜션|시설|곳)", quote)
    if named and changes.name_query != named[1].strip("'\"‘’“”"):
        raise ValueError("an explicit place name must not be dropped")
    # Reject reported/quoted/hypothetical commands. Literal quoted place names are data.
    text = query
    names = [changes.name_query] if changes.name_query else []
    if intent.bookmark and intent.bookmark.target.kind == "name":
        names.append(intent.bookmark.target.text)
    for name in names:
        for left, right in [("'", "'"), ('"', '"'), ("‘", "’"), ("“", "”")]:
            text = text.replace(left + name + right, "상호")
        if named and name == named[1].strip("'\"‘’“”"):
            text = text.replace(name + "라는", "상호라는").replace(name + "이라는", "상호이라는")
    if re.search(
        r"""["'“”‘’]|라고\s*(?:했|하|말)|라면|다면|면\s*(?:어떻게|뭐)|말라는|하지\s*마""", text
    ):
        raise ValueError("quoted or hypothetical commands have no authority")
    # A save prohibition may accompany a separate search, but cannot authorize a save.
    checked = text
    if intent.forbid_save and not intent.bookmark:
        checked = re.sub(r"(?:찜|저장)\s*하지\s*말고", "", checked)
    if re.search(
        r"(?:찾|보여|추천|골라|선택|적용|변경|찜|저장|제외)[가-힣 ]*(?:하지\s*말|하지\s*마|지\s*말|지\s*마)",
        checked,
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
