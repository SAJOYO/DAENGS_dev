"""Current display facts, with explicit reference scope. No inferred click preferences."""

from daengs_place.place.conversation.contract import NamedPlace


def identity(key):
    return key.source, key.ref


def unique_keys(keys):
    return tuple({identity(key): key for key in keys}.values())


def current_places(request):
    snapshot = request.previous.snapshot
    if snapshot is None:
        if request.visible_order or request.visible_selected:
            raise ValueError("display without snapshot")
        return ()
    available = {
        identity(hit.place.key): hit.place
        for group in snapshot.result.groups
        for hit in group.matched
    }
    order = request.visible_order or snapshot.display_order
    if len(unique_keys(order)) != len(order) or any(
        identity(key) not in available for key in order
    ):
        raise ValueError("display is outside saved snapshot")
    selected = request.visible_selected or request.previous.selected
    if selected and identity(selected) not in available:
        raise ValueError("selection is outside saved snapshot")
    return tuple(available[identity(key)] for key in order)


def screen_context(request):
    places = current_places(request)
    selected = request.visible_selected or request.previous.selected
    return {
        "current_places": [
            {"index": i, "key": p.key.model_dump(), "name": p.name, "kind": p.match.kind}
            for i, p in enumerate(places, 1)
        ],
        "selected": selected.model_dump() if selected else None,
        "excluded_places": [
            {"index": i, **p.model_dump(mode="json")}
            for i, p in enumerate(request.previous.exploration.excluded, 1)
        ],
    }


def edit_exclusions(request, intent):
    old = request.previous.exploration.excluded if request.previous else ()
    if intent and intent.browse == "restart":
        return (), (), old
    edit = intent.place_edit if intent else None
    if edit is None:
        return old, (), ()
    places = (
        tuple(NamedPlace(key=p.key, name=p.name) for p in current_places(request))
        if edit.operation == "exclude"
        else old
    )
    if any(i > len(places) for i in edit.indices):
        raise ValueError("place edit reference outside displayed scope")
    targets = tuple(places[i - 1] for i in dict.fromkeys(edit.indices))
    previous = {identity(p.key): p for p in old}
    if edit.operation == "exclude":
        added = tuple(p for p in targets if identity(p.key) not in previous)
        result = (*old, *added)
        if len(result) > 120:
            raise ValueError("exclusion budget reached")
        return result, added, ()
    removed = {identity(p.key) for p in targets}
    return tuple(p for p in old if identity(p.key) not in removed), (), targets
