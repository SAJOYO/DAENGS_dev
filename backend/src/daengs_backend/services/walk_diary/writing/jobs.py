"""Strategy-specific inputs and response validation over immutable card contracts."""

import json

from daengs_backend.services.walk_diary import space_details
from daengs_backend.services.walk_diary.contracts import (
    ActionProse,
    CardTitle,
    CardTitles,
    SpaceProse,
    WritingJob,
)
from daengs_backend.services.walk_diary.model_input import normalize
from daengs_backend.services.walk_diary.writing import policy
from daengs_backend.services.walk_diary.writing.context import get_action_context, get_space_context
from daengs_walk.diary.board.action_context import require_action
from daengs_walk.diary.board.activity import movement_uses
from daengs_walk.diary.board.title_context import (
    CONTENT_BASIS,
    generated_body,
    title_context,
    title_revision,
)
from daengs_walk.diary.contracts.input import digest


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


def _require_selected(base, scene, stamp):
    if scene not in base.board.scenes or (
        stamp is not None and (stamp.scene_id != scene.id or stamp not in base.slots.stamps)
    ):
        raise ValueError("writing job requires the selected scene and stamp")


def action_job(base, scene, stamp=None):
    _require_selected(base, scene, stamp)
    context = get_action_context(base, scene.id)
    if context is None:
        return None
    return job(context.stage, context.request).model_copy(update={"evidence": context.evidence})


def space_job(base, scene, stamp):
    _require_selected(base, scene, stamp)
    context = get_space_context(base, scene.id)
    return job(context.stage, context.request).model_copy(update={"evidence": context.evidence})


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
        if item.tool_trace is not None and item.stage != "space":
            raise ValueError("space tools belong only to the space writer")
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
                if item.tool_trace is not None:
                    model = normalize("space", item.request)
                    space_details.validate_citations(
                        model.payload, model.references, item.tool_trace, output.evidence_ids
                    )
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
