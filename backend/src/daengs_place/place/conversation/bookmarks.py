"""Prepare one grounded command; only the app's member bookmark path can execute it."""

import re

from daengs_place.place.conversation.context import current_places
from daengs_place.place.conversation.contract import BookmarkCommand, NamedPlace
from daengs_place.place.conversation.grounding import compact, resolve_target
from daengs_place.place.conversation.intent import SemanticChanges

SOURCES = {"kcisa", "kto", "public:mois:animal_hospital", "public:mois:animal_pharmacy"}
# A deliberately bounded *whole request* grammar after removing the literal target.
# It never accepts a quoted instruction, a negation, or a second clause by substring.
SAVE = r"(?:찜(?:해줘|해주세요|해두어?줘|해둬|해둬줘|해놓아?줘|해놔줘)|저장(?:해줘|해주세요)|남겨(?:둬|줘))"
REMOVE = r"(?:찜(?:을|에서)?(?:해제해줘|해제해주세요|지워줘|삭제해줘|빼줘)|찜해제)"


def grounded_command(request, intent):
    edit = intent.bookmark
    if (
        (intent.forbid_save and edit.operation == "save")
        or intent.navigation != "stay"
        or intent.search_scope != "keep"
        or intent.changes != SemanticChanges()
        or intent.browse != "current"
        or intent.place_edit
        or intent.refresh
        or intent.unsupported
        or intent.region_query
        or intent.unresolved != "none"
        or edit.target.kind == "all"
    ):
        raise ValueError("bookmark composition is not supported in v1")
    target = edit.target
    # Only the literal name may be quoted. Strip that pair before reference resolution.
    query = request.query.strip().rstrip(".!~ ")
    if target.kind == "name":
        for left, right in [("'", "'"), ('"', '"'), ("‘", "’"), ("“", "”")]:
            query = query.replace(left + target.text + right, target.text)
    prefix = re.escape(target.text).replace(r"\ ", r"\s*")
    match = re.fullmatch(prefix + r"\s*(?:은|는|을|를|만)?\s*(.+)", query)
    operation = compact(match[1]) if match else ""
    pattern = SAVE if edit.operation == "save" else REMOVE
    quote = compact(edit.operation_quote)
    if (
        not re.fullmatch(pattern, operation)
        or quote not in compact(request.query)
        or not (operation in quote or quote in operation)
    ):
        raise ValueError("not one explicit positive bookmark instruction")
    places = tuple(NamedPlace(key=p.key, name=p.name) for p in current_places(request))
    selected = request.visible_selected or request.previous.selected
    # The whole-request match already verified the literal target and its particle.
    # Normalize only that separator for the shared target resolver (e.g. 상호명'만).
    place = resolve_target(target.text + " " + match[1], target, places, selected)[0]
    if place.key.source not in SOURCES:
        raise ValueError("unsupported bookmark source")
    return BookmarkCommand(key=place.key, name=place.name, saved=edit.operation == "save")


def prepare_bookmark(request, intent, unchanged):
    if request.bookmark_commands != "v1":
        return unchanged(
            request,
            "clarify",
            "bookmark_client_required",
            "찜은 장소 카드의 하트로 저장·해제할 수 있어요. 말로 찜하려면 앱을 업데이트해 주세요.",
        )
    try:
        command = grounded_command(request, intent)
    except ValueError:
        return unchanged(
            request,
            "clarify",
            "bookmark_needs_explicit_target",
            "현재 목록의 한 장소를 골라 찜 저장 또는 해제를 부탁해 주세요. 다른 요청은 따로 말해 주세요.",
        )
    result = unchanged(request, "edit_only", "bookmark_prepared", "", action="execute")
    # Preserve the actual selection instead of making a bookmark pick another card.
    selected = request.visible_selected or request.previous.selected
    return result.model_copy(
        update={
            "state": result.state.model_copy(update={"selected": selected}),
            "receipt": result.receipt.model_copy(
                update={
                    "selected": selected,
                    "bookmark_command": command,
                }
            ),
        }
    )
