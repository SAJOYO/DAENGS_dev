"""Title dependencies are generated parts, never the separately preserved user note."""

from daengs_walk.diary.contracts.input import digest

CONTENT_BASIS = "generated-parts-v1"


def generated_body(scene):
    parts = scene.writing
    return "\n".join(
        p
        for p in [
            parts.observation.text
            if parts.observation and not parts.observation_in_activity
            else "",
            parts.space.text,
            *(a.text for a in parts.actions),
        ]
        if p
    )


def title_revision(scene):
    parts = scene.writing
    return digest(
        [
            CONTENT_BASIS,
            scene.id,
            scene.anchor.model_dump(mode="json"),
            [p.model_dump(mode="json") for p in scene.place_reference],
            parts.space.model_dump(mode="json"),
            [p.model_dump(mode="json") for p in parts.actions],
            parts.observation.model_dump(mode="json") if parts.observation else None,
            parts.observation_in_activity,
        ]
    )


def title_context(cards):
    return [
        {
            "card_id": c.id,
            "order": c.order,
            "event_at": c.anchor.event_at.isoformat(),
            "body": generated_body(c),
            "location": [p.model_dump(mode="json") for p in c.place_reference],
        }
        for c in cards
    ]
