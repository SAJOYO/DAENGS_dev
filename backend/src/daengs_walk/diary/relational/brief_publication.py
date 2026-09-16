"""v8 reads validate frozen values; no planning, source enrichment or model calls."""

import json

from daengs_walk.diary.relational.brief_contracts import BriefDeliveryState, DeliveredMeaning
from daengs_walk.diary.relational.brief_response import (
    brief_request_revision,
    brief_response_schema,
    parse_brief,
    resolve_brief_answer,
)
from daengs_walk.diary.relational.contracts import SemanticReview, WriterTask
from daengs_walk.diary.relational.scene_comparison_contracts import SpaceComparisonInput
from daengs_walk.diary.relational.title_context import validate_title_publication
from daengs_walk.diary.relational.writer_view import publication_writer_view
from daengs_walk.diary.relational.writing_brief import advance_brief_delivery
from daengs_walk.value_contracts import digest

BRIEF_PUBLICATION = "relational-diary-skeleton-v8"


def validate_brief_result(task, result, *, execution_review=None):
    brief = parse_brief(task.payload)
    if any(result[k] != getattr(task, k) for k in ("scene_id", "stage", "revision")):
        raise ValueError("brief result belongs to another task")
    request = publication_writer_view(brief, result["policy"])
    schema = brief_response_schema(brief, result["policy"])
    if result.get("request") != request or result.get("response_schema") != schema:
        raise ValueError("writer did not receive the canonical brief")
    if result["request_revision"] != brief_request_revision(
        result["policy"], result["prompt_revision"], request, schema
    ):
        raise ValueError("brief request binding changed")
    review_enabled = result["review_enabled"]
    if type(review_enabled) is not bool or (
        execution_review is not None and review_enabled != execution_review
    ):
        raise ValueError("brief review policy changed")
    if result["status"] == "failed":
        if result.get("answer") is not None:
            raise ValueError("failed brief cannot contain an accepted answer")
        return None
    if result["status"] != "returned":
        raise ValueError("unknown brief result status")
    answer = resolve_brief_answer(brief, json.loads(result["raw_text"]), result["policy"])
    if (
        answer.model_dump(mode="json") != result["answer"]
        or result["candidate"] != result["answer"]
    ):
        raise ValueError("accepted answer differs from the raw brief response")
    if not review_enabled:
        if result["semantic_status"] != "unverified" or "semantic_review" in result:
            raise ValueError("invalid unreviewed brief")
    else:
        review = result["semantic_review"]
        required = [brief.required_event.id] if task.stage == "action" else []
        expected = {
            "part": task.stage,
            "evidence": request,
            "candidate": result["answer"],
            "required_evidence_ids": required,
        }
        assessment = SemanticReview.model_validate(review["assessment"])
        used = set(assessment.used_evidence_ids)
        if (
            result["semantic_status"] != "model_reviewed"
            or review["request"] != expected
            or review["status"] != "passed"
            or not assessment.passes
            or not used
            or len(used) != len(assessment.used_evidence_ids)
            or not set(required) <= used <= set(answer.evidence_ids)
            or SemanticReview.model_validate(json.loads(review["raw_text"])) != assessment
        ):
            raise ValueError("published brief has no matching passed review")
    return answer


def validate_brief_publication(prepared, receipt):
    """Compare stored frames/plans/requests/results. Never recompute a scene plan."""
    snapshot, writing = prepared["snapshot"], receipt["writing"]
    if (
        receipt["version"] != BRIEF_PUBLICATION
        or snapshot.get("writing_brief_version") != "writing-brief-preparation-v1"
        or digest(snapshot) != prepared["revision"]
        or receipt["snapshot_revision"] != prepared["revision"]
        or writing["snapshot_revision"] != prepared["revision"]
        or receipt["input_revision"] != snapshot["input_revision"]
    ):
        raise ValueError("stored brief snapshot binding changed")
    results = {r["task_id"]: r for r in writing["results"]}
    if len(results) != len(writing["results"]):
        raise ValueError("duplicate saved brief result")
    execution = receipt.get("execution", {})
    if execution and execution["model"] != writing["model"]:
        raise ValueError("stored execution model changed")
    used, seen, memory, previous = set(), set(), BriefDeliveryState(), None
    for frame, plan, card in zip(
        snapshot["frames"], snapshot["plans"], receipt["cards"], strict=True
    ):
        scene_id = card["scene_id"]
        if scene_id in seen or scene_id != frame["scene_id"] or scene_id != plan["scene_id"]:
            raise ValueError("duplicate or mismatched published scene")
        seen.add(scene_id)
        if plan["revision"] != digest({k: v for k, v in plan.items() if k != "revision"}):
            raise ValueError("stored plan binding changed")
        for key in ("anchor", "standalone_context", "relation_slots", "movement_observations"):
            if card[key] != plan[key]:
                raise ValueError("stored card differs from frozen plan")
        if card["anchor"] != frame["anchor"] or card["originals"] != [
            o for o in snapshot["originals"] if o["scene_id"] == scene_id
        ]:
            raise ValueError("stored anchor or original changed")
        comparison = card["comparison"]
        context = SpaceComparisonInput.model_validate(comparison["context"])
        if (
            comparison["version"] != "brief-comparison-publication-v1"
            or comparison["header"] != frame["card_header"]
            or context.current.model_dump(mode="json") != frame["scene_snapshot"]
            or (context.earlier.model_dump(mode="json") if context.earlier else None) != previous
            or context.relation_slots.model_dump(mode="json") != frame["spatial_comparison_slots"]
        ):
            raise ValueError("stored comparison or header changed")
        previous = frame["scene_snapshot"]
        brief = parse_brief(card["space_brief"])
        before = memory if writing["short_memory_enabled"] else BriefDeliveryState()
        if (
            brief.context.model_dump(mode="json") != frame["narrative_context"]
            or brief.delivery != before
            or plan["delivery_before"] != before.model_dump(mode="json")
            or card["action_brief"] != frame["action_brief"]
        ):
            raise ValueError("stored brief or delivery chain changed")
        selection, parts = None, {}
        for stage in ("space", "action"):
            raw_task = plan[stage + "_task"]
            task = WriterTask.model_validate(raw_task) if raw_task else None
            result, answer = None, None
            if task:
                if task.id in used or (task.scene_id, task.stage) != (scene_id, stage):
                    raise ValueError("reused or mismatched brief task")
                used.add(task.id)
                if task.payload != card[stage + "_brief"]:
                    raise ValueError("stored task read a different brief")
                result = results[task.id]
                if result["policy"] != writing["policy"]:
                    raise ValueError("stored writing policy changed")
                answer = validate_brief_result(
                    task, result, execution_review=execution.get("semantic_review_enabled")
                )
            elif stage == "action" and card["action_brief"] is not None:
                raise ValueError("current event task was dropped")
            parts[stage] = {
                "status": result["status"] if result else "not_requested",
                "text": answer.text if answer else "",
                "task_id": task.id if task else None,
            }
            if stage == "space" and answer:
                selection = DeliveredMeaning(
                    context=brief.context,
                    evidence_ids=answer.evidence_ids,
                    relation_ids=answer.relation_ids,
                    semantic_status=result["semantic_status"],
                )
        if card["parts"] != parts or card["body"] != "\n".join(
            p["text"] for p in parts.values() if p["text"]
        ):
            raise ValueError("stored body differs from accepted results")
        selected = {
            **plan["relation_selection"],
            "space": list(selection.relation_ids) if selection else [],
        }
        if card["relation_selection"] != selected:
            raise ValueError("stored relation selection changed")
        memory = advance_brief_delivery(brief, selection)
        if (
            card["delivery_after"] != memory.model_dump(mode="json")
            or card["delivery_after"] != plan["delivery_after"]
        ):
            raise ValueError("stored delivered meanings changed")
    if used != results.keys() or receipt["delivered_task_ids"] != [
        k for k, r in results.items() if r["status"] == "returned"
    ]:
        raise ValueError("unexpected saved brief results")
    validate_title_publication(receipt)
