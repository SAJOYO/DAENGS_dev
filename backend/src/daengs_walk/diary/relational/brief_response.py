"""One citation contract for brief requests, response schemas and accepted results."""

from daengs_walk.diary.relational.brief_contracts import ActionWritingBrief, SpaceWritingBrief
from daengs_walk.diary.relational.contracts import WriterAnswer
from daengs_walk.diary.relational.scene_comparison_contracts import SpaceComparisonAnswer
from daengs_walk.diary.relational.writer_material_policy import WRITER_POLICY, writer_reference_ids
from daengs_walk.value_contracts import digest


def brief_request_revision(policy, prompt_revision, request, schema):
    """Frozen request binding, independent of subsequently edited prompts."""
    return digest([policy, prompt_revision, request, schema])


def parse_brief(payload):
    if payload.get("version") == "space-writing-brief-v1":
        return SpaceWritingBrief.model_validate(payload)
    if payload.get("version") == "action-writing-brief-v1":
        return ActionWritingBrief.model_validate(payload)
    raise ValueError("unsupported writing brief")


def brief_response_schema(brief, policy=WRITER_POLICY):
    citation_ids, relation_ids = writer_reference_ids(brief, policy)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 220},
            "evidence_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "enum": list(citation_ids)},
            },
        },
        "required": ["text", "evidence_ids"],
    }
    if isinstance(brief, SpaceWritingBrief):
        relations = {"type": "array", "uniqueItems": True, "items": {"type": "string"}}
        if relation_ids:
            relations["items"]["enum"] = list(relation_ids)
        else:
            relations["maxItems"] = 0
        schema["properties"].update(
            focus={"type": "string", "minLength": 1, "maxLength": 160}, relation_ids=relations
        )
        schema["required"] += ["focus", "relation_ids"]
    return schema


def resolve_brief_answer(brief, value, policy=WRITER_POLICY):
    citation_ids, relation_ids = writer_reference_ids(brief, policy)
    space = isinstance(brief, SpaceWritingBrief)
    answer = (SpaceComparisonAnswer if space else WriterAnswer).model_validate(value)
    cited = set(answer.evidence_ids)
    if (
        not answer.text.strip()
        or not cited
        or len(cited) != len(answer.evidence_ids)
        or not cited <= set(citation_ids)
    ):
        raise ValueError("invalid brief evidence references")
    if space:
        if (
            not answer.focus.strip()
            or len(set(answer.relation_ids)) != len(answer.relation_ids)
            or not set(answer.relation_ids) <= set(relation_ids)
        ):
            raise ValueError("invalid narrative relation references")
    elif brief.required_event.id not in cited:
        raise ValueError("required dog event was omitted")
    return answer
