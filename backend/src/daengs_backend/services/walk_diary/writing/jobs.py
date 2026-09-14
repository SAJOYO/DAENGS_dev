"""Strategy-specific inputs and response validation over immutable card contracts."""

import json

from daengs_backend.services.walk_diary.contracts import (
    ActionProse,
    CardTitle,
    CardTitles,
    SpaceProse,
    WritingJob,
)
from daengs_backend.services.walk_diary.writing import policy
from daengs_walk.diary.board.action_context import pin_movement, require_action
from daengs_walk.diary.board.activity import movement_uses
from daengs_walk.diary.board.scene_input import action_anchor
from daengs_walk.diary.board.title_context import (
    CONTENT_BASIS,
    generated_body,
    title_context,
    title_revision,
)
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.slots.space import writing_facts


def job(stage, payload):
    if stage == "action":
        require_action(payload)
    # Only this strategy's actual dependencies belong in its revision, never the whole board.
    revision = digest(
        {
            "strategy": policy.writing_version()["prompts"][stage],
            "model": policy.MODEL,
            "input": payload,
        }
    )
    return WritingJob(
        stage=stage, request_revision=revision, request={**payload, "request_revision": revision}
    )


def common_context(base):
    names = dict(base.input.pet_names)
    return {
        "record_kind": "guardian_walk_diary",
        "companions": [
            {"id": pet_id, "name": names.get(pet_id)} for pet_id in base.input.source.pet_ids
        ],
    }


def action_job(base, scene, stamp=None):
    """Only a live behavior pin can create an action job; movement cannot open one."""
    anchor = action_anchor(scene)
    if anchor is None:
        return None
    stamp = stamp or next(s for s in base.slots.stamps if s.scene_id == scene.id)
    movement = pin_movement(
        [{"id": e.id, "facts": e.facts} for e in stamp.evidence if e.role == "scene_movement"]
    )
    pet_id = scene.core.record.content.pet_id if anchor else None
    if pet_id is not None and pet_id not in base.input.source.pet_ids:
        raise ValueError("action actor is outside this walk")
    actor = {"id": pet_id, "name": dict(base.input.pet_names).get(pet_id)}
    value = job(
        "action",
        {
            "card_id": scene.id,
            "event_at": scene.anchor.event_at.isoformat(),
            "walk_context": common_context(base),
            "action": {**anchor, "actor": actor} if anchor else None,
            **({"movement": movement} if movement else {}),
        },
    )
    return value.model_copy(
        update={
            "evidence": {
                e.id: e.model_dump(mode="json")
                for e in stamp.evidence
                if e.role == "scene_movement"
            }
        }
    )


def space_job(base, scene, stamp):
    # Motion/actor/notes are not spatial observations. They stay in their source records.
    materials, evidence = [], {}
    for e in stamp.materials():
        if e.part not in {"space", "environment"}:
            continue
        facts = {k: v for k, v in writing_facts(e).items() if k != "retrieved_at"}
        if e.facts.get("source") == "land_cover" and isinstance(facts.get("material"), dict):
            facts["material"] = {
                k: policy.LAND_WORDS.get(v, v) for k, v in facts["material"].items()
            }
        identity = "material:" + digest([e.role, facts])
        materials.append({"id": identity, "role": e.role, "facts": facts})
        evidence[identity] = e.model_dump(mode="json")
    materials.sort(key=lambda material: material["id"])
    known = {e.role for e in stamp.materials()}
    backgrounds = [
        b
        for b in (
            *base.input.source.backgrounds,
            *(base.scene_backgrounds.backgrounds if base.scene_backgrounds else ()),
        )
        if b.target == scene.core_ref
    ]

    def state(providers, available):
        matching = [b for b in backgrounds if b.provider in providers]
        return "known" if available else (matching[-1].status if matching else "unavailable")

    def ground(material):
        relation = material["facts"].get("relation")
        return isinstance(relation, dict) and relation.get("kind") == "land_cover_at_query_point"

    value = job(
        "space",
        {
            "card_id": scene.id,
            "walk_context": common_context(base),
            "anchor": scene.anchor.model_dump(mode="json"),
            "sources": {
                "sgis": state({"sgis"}, "scene_address_reference" in known),
                "egis": state(
                    {"public-normalized-land_cover"},
                    any(e.facts.get("source") == "land_cover" for e in stamp.materials()),
                ),
                "environment": "known"
                if any(e.part == "environment" for e in stamp.materials())
                else "unavailable",
            },
            "materials": materials,
            "scene_structure": {
                "current_ground": [m["id"] for m in materials if ground(m)],
                "administrative_location": [
                    m["id"] for m in materials if m["role"] == "scene_address_reference"
                ],
                "local_details": [
                    m["id"]
                    for m in materials
                    if m["role"] != "scene_address_reference" and not ground(m)
                ],
            },
        },
    )
    return value.model_copy(update={"evidence": evidence})


def title_jobs(cards):
    context = title_context(cards)
    payloads = [
        {
            "card_id": c.id,
            "content_revision": title_revision(c),
            "space": c.writing.space.model_dump(mode="json"),
            "actions": [a.model_dump(mode="json") for a in c.writing.actions],
            "place_reference": [p.model_dump(mode="json") for p in c.place_reference],
            "event_at": c.anchor.event_at.isoformat(),
            **(
                {"observation": c.writing.observation.model_dump(mode="json")}
                if c.writing.observation
                else {}
            ),
        }
        for c in cards
        if generated_body(c).strip()
    ]
    return [
        job(
            "title",
            {
                "cards": payloads[i : i + policy.MAX_CARDS],
                "content_basis": CONTENT_BASIS,
                "context": context,
                "context_revision": digest(context),
            },
        )
        for i in range(0, len(payloads), policy.MAX_CARDS)
    ]


def validate_output(item, raw):
    schema = {"space": SpaceProse, "action": ActionProse, "title": CardTitles}[item.stage]
    try:
        if isinstance(raw, str):
            if len(raw.encode()) > 64_000:
                raise ValueError("response exceeds budget")
            raw = json.loads(raw)
        if item.stage == "title":
            if not isinstance(raw, dict) or set(raw) != {"titles"}:
                raise ValueError("invalid title envelope")
            entries = raw["titles"]
            if not isinstance(entries, (list, tuple)) or len(entries) > policy.MAX_CARDS:
                raise ValueError("invalid title batch")
            parsed, counts = [], {}
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("card_id"), str):
                    key = entry["card_id"]
                    counts[key] = counts.get(key, 0) + 1
                try:
                    parsed.append(CardTitle.model_validate(entry))
                except (ValueError, TypeError):
                    continue
            output = CardTitles(titles=tuple(t for t in parsed if counts[t.card_id] == 1))
        else:
            output = schema.model_validate(raw)
        if item.stage != "title":
            if (
                output.card_id != item.request["card_id"]
                or output.request_revision != item.request_revision
            ):
                raise ValueError("writing result belongs to another request")
            if item.stage == "action":
                action = require_action(item.request)
                refs = set(output.movement_ids)
                if (
                    output.action_id != (action["id"] if action else None)
                    or not output.text.strip()
                    or len(refs) != len(output.movement_ids)
                    or not refs <= {u["id"] for u in movement_uses(item.request)}
                    or (not action and not refs)
                ):
                    raise ValueError("action changed")
            else:
                refs = set(output.evidence_ids)
                if (
                    len(refs) != len(output.evidence_ids)
                    or not refs <= {e["id"] for e in item.request["materials"]}
                    or bool(output.text.strip()) != bool(refs)
                ):
                    raise ValueError("space citation changed")
                names = [c["name"] for c in item.request["walk_context"]["companions"] if c["name"]]
                if any(name in output.text for name in names):
                    raise ValueError("companion name leaked into space")
        else:
            expected = {c["card_id"]: c["content_revision"] for c in item.request["cards"]}
            # A readable batch is adopted per card; an invalid sibling cannot erase good titles.
            counts = {}
            for title in output.titles:
                counts[title.card_id] = counts.get(title.card_id, 0) + 1
            output = CardTitles(
                titles=tuple(
                    t
                    for t in output.titles
                    if counts[t.card_id] == 1
                    and expected.get(t.card_id) == t.content_revision
                    and t.text.strip()
                )
            )
        return item.model_copy(update={"accepted": output.model_dump(mode="json")})
    except (ValueError, TypeError, KeyError):
        return item.model_copy(update={"failure_code": "invalid_response"})
