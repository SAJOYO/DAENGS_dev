"""Project only the phase containing this pin, with separate pace and shape."""

from daengs_walk.diary.route.pin_context import SHAPE_TEXT, movement_uses, pin_movement


def current_motion(request):
    uses = movement_uses(request)
    uses = [u for u in uses if u["from_s"] <= 0 < u["to_s"] and u.get("at_s", 0) == 0]
    if len({(u["from_s"], u["to_s"]) for u in uses}) > 1:
        uses = []
    result = {"current_gait": [], "current_shape": []}
    for use in uses:
        code = use["meaning"]
        if code in {"relative_slow", "relative_fast"}:
            key = "current_gait"
            meaning = (
                "이번 산책 기준보다 느린 걸음"
                if code == "relative_slow"
                else "이번 산책 기준보다 빠른 걸음"
            )
        elif code in SHAPE_TEXT:
            key, meaning = "current_shape", SHAPE_TEXT[code]
        else:
            # Retrace requires a prior-route relationship; not current action material.
            continue
        result[key].append(
            {
                "id": use["id"],
                "meaning": meaning,
                "from_pin_s": use["from_s"],
                "to_pin_s": use["to_s"],
                "event_at_pin_s": use.get("at_s"),
                "relation": "행동핀 시점에 유효한 산책 이동 상황. 행동의 원인이나 지속시간은 아님",
            }
        )
    for key, items in result.items():
        if len({x["meaning"] for x in items}) > 1:
            result[key] = []
    return result


def current_background(snapshot):
    """Current point background for behavior, derived from the authoritative scene snapshot."""
    return [
        {
            "id": fact["id"],
            "role": "point_land_cover",
            "material": {"피복": fact["value"]["피복"]},
            "relation": fact["scope"]["description"],
            "time_meaning": fact["time_meaning"],
        }
        for fact in snapshot["facts"]
        if fact["family"] == "land_cover"
    ]


def current_action(scene, source, pet_names, evidence):
    """An existing current behavior record is the sole trigger, even at zero slot capacity."""
    record = getattr(scene.core, "record", None)
    if record is None or record.deleted or record.content.kind != "behavior":
        return None
    content = record.content
    if content.pet_id is not None and content.pet_id not in source.pet_ids:
        raise ValueError("action actor is outside this walk")
    meanings = {"sniffing": "냄새 맡기", "excretion": "배변", "barking": "짖기"}
    movement = pin_movement(
        [{"id": e.id, "facts": e.facts} for e in evidence if e.role == "scene_movement"]
    )
    return {
        "recorded_action": {
            "id": "a1",
            "actor": dict(pet_names).get(content.pet_id),
            "action": meanings[content.code],
        },
        **current_motion({"movement": movement}),
    }


def build_action_brief(scene, source, pet_names, evidence, context):
    """Build from a live source pin; prior actions/narrator lists have no input field."""
    from daengs_walk.diary.relational.brief_contracts import (
        ActionWritingBrief,
        CurrentDogEvent,
        DogActor,
        EventContext,
    )
    from daengs_walk.diary.relational.contracts import CurrentMotion
    from daengs_walk.value_contracts import digest

    record = getattr(scene.core, "record", None)
    if record is None or record.deleted or record.content.kind != "behavior":
        return None
    position = context.current.position
    if (
        position.timeline.source_revision != source.revision()
        or position.scene_id != scene.id
        or position.recorded_at != scene.anchor.event_at
        or scene.anchor != record.anchor
        or context.current.point != scene.anchor.point
        or context.current.position_basis != scene.anchor.method
        or context.current.accuracy_m != scene.anchor.accuracy_m
    ):
        raise ValueError("current event does not match the source scene")
    if record not in source.records:
        raise ValueError("event is not the current source record version")
    content = record.content
    if content.pet_id is not None and content.pet_id not in source.pet_ids:
        raise ValueError("action actor is outside this walk")
    event = CurrentDogEvent(
        id="event:" + digest(record.ref),
        source_record=record.ref,
        actor=DogActor(
            pet_id=content.pet_id,
            name=dict(pet_names).get(content.pet_id) if content.pet_id else None,
        ),
        behavior=content.code,
        anchor=record.anchor,
        scene_id=scene.id,
    )
    options = [
        EventContext(for_event_id=event.id, evidence=fact, kind="space", subject="record_location")
        for fact in context.current_facts
        if fact.meaning.kind in {"road", "land_cover"}
    ]
    # The existing extraction already selects only the motion phase containing this pin.
    motion = current_action(scene, source, pet_names, evidence)
    for kind in ("current_gait", "current_shape"):
        for item in motion[kind]:
            item = {**item, "id": "motion:" + digest([event.id, kind, item])}
            options.append(
                EventContext(
                    for_event_id=event.id,
                    evidence=CurrentMotion(**item),
                    kind=kind,
                    subject="recording_device",
                )
            )
    return ActionWritingBrief(
        position=position, required_event=event, context_options=tuple(options)
    )
