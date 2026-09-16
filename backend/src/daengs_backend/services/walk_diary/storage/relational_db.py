"""Versioned JSONB envelope and deterministic public projection of frozen v7/v8 receipts."""

from copy import deepcopy

from daengs_backend.schemas.walk_relational_diary import RELATIONAL_STORAGE, RelationalBundle
from daengs_backend.services.walk_diary.relational_execution import RelationalDiaryResult
from daengs_walk.diary.relational.brief_publication import (
    BRIEF_PUBLICATION,
    validate_brief_publication,
)
from daengs_walk.diary.relational.publication import PUBLICATION_VERSION, validate_publication
from daengs_walk.diary.relational.title_context import validate_title_publication
from daengs_walk.value_contracts import digest


def source_revision(assembled):
    """User input changes invalidate publication; asynchronous background enrichment does not."""
    source = assembled.source.model_dump(
        mode="json",
        exclude={
            "backgrounds",
            "selected_background_ids",
            "scene_policy_version",
            "writing_policy_version",
        },
    )
    source["records"].sort(key=lambda r: (r["ref"]["store"], r["ref"]["id"]))
    source["observations"].sort(key=lambda o: o["id"])
    source["pet_ids"].sort()
    return digest({"source": source, "pet_names": sorted(assembled.pet_names)})


def project(receipt, session_id):
    results = {r["task_id"]: r for r in receipt["writing"]["results"]}
    cards = []
    for card in receipt["cards"]:
        scene_title = receipt.get("scene_titles", {}).get(card["scene_id"], {})
        comparison = card["comparison"]
        parts = {}
        for stage in ("space", "action"):
            part = card["parts"][stage]
            result = results.get(part["task_id"])
            parts[stage] = {
                "text": part["text"],
                "status": part["status"],
                "semantic_status": result["semantic_status"]
                if result and result["status"] == "returned"
                else "not_published",
            }
        context = comparison["context"]
        cards.append(
            {
                "scene_id": card["scene_id"],
                "title": scene_title.get("text")
                if scene_title.get("status") == "returned"
                else None,
                "title_status": scene_title.get("status", "not_requested"),
                "anchor": card["anchor"],
                "header": comparison["header"],
                **parts,
                "body": card["body"],
                "current_context": context["current"],
                "comparison_scene_id": context["earlier"]["scene_id"]
                if context["earlier"]
                else None,
                "originals": [r["record"] for r in card["originals"]],
            }
        )
    title = receipt.get("title", {})
    return RelationalBundle(
        client_session_id=session_id,
        cards=cards,
        title=title.get("text") if title.get("status") == "returned" else None,
        title_status=title.get("status", "not_requested"),
    )


def store_result(result, base, *, revision, generation, target):
    from daengs_backend.services.walk_diary.writing.relational import validate_prepared
    from daengs_walk.diary.relational.assembly import assemble_receipt

    if not isinstance(result, RelationalDiaryResult):
        raise TypeError("relational result required")
    if (
        result.input_revision != base.input.source.revision()
        or result.board_revision != base.board.plan_revision
        or result.prepared["snapshot"]["input_revision"] != result.input_revision
        or result.prepared["snapshot"]["board_revision"] != result.board_revision
    ):
        raise ValueError("writer result belongs to another reservation")
    validate_prepared(result.prepared)
    expected = assemble_receipt(result.prepared, result.receipt["writing"])
    if result.receipt.get("version") not in {PUBLICATION_VERSION, BRIEF_PUBLICATION} or any(
        result.receipt.get(k) != value for k, value in expected.items()
    ):
        raise ValueError("result differs from canonical publication")
    validate_title_publication(result.receipt)
    if result.receipt["version"] == BRIEF_PUBLICATION:
        validate_brief_publication(result.prepared, result.receipt)
    body = {
        "source_revision": revision,
        "generation": generation,
        "target": target,
        "walk_id": base.input.source.walk_id,
        "session_id": base.input.source.client_session_id,
        "prepared": deepcopy(result.prepared),
        "receipt": deepcopy(result.receipt),
    }
    body["public"] = project(body["receipt"], body["session_id"]).model_dump(mode="json")
    return {"format": RELATIONAL_STORAGE, "payload": body, "digest": digest(body)}


def read_result(raw, *, walk_id, session_id, revision, generation):
    """Validate saved values only: never run current selection, planning or a writer."""
    if not isinstance(raw, dict) or raw.get("format") != RELATIONAL_STORAGE:
        raise ValueError("unsupported relational storage")
    body = raw["payload"]
    if digest(body) != raw["digest"] or (
        body["walk_id"],
        body["session_id"],
        body["source_revision"],
        body["generation"],
    ) != (str(walk_id), str(session_id), revision, generation):
        raise ValueError("stored relational binding changed")
    version = body["receipt"].get("version")
    if version == BRIEF_PUBLICATION:
        validate_brief_publication(body["prepared"], body["receipt"])
    elif version == PUBLICATION_VERSION:
        validate_publication(body["receipt"])
    else:
        raise ValueError("unsupported relational publication")
    value = project(body["receipt"], session_id)
    # Historical v1 cards omitted the additive title fields. Normalize only those
    # defaults; all actual saved values still compare byte-for-value.
    saved_public = deepcopy(body["public"])
    for card in saved_public["cards"]:
        card.setdefault("title", None)
        card.setdefault("title_status", "not_requested")
    if value.model_dump(mode="json") != saved_public:
        raise ValueError("stored public projection changed")
    return value
