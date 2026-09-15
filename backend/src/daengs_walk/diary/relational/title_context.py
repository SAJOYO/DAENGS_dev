"""Title reads only adopted prose and its scene ownership; no sources or I/O."""

import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from daengs_walk.diary.relational.contracts import SemanticReview
from daengs_walk.diary.relational.title_writer_view import (
    TITLE_WRITER_POLICY,
    title_publication_view,
)
from daengs_walk.value_contracts import Instant, ValueContract, digest

TITLE_CONTRACT = "relational-title-readmodel-v1"


class TitleObservation(ValueContract):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class TitleScene(ValueContract):
    scene_id: str = Field(min_length=1)
    order: int = Field(ge=1, strict=True)
    recorded_at: Instant
    space: str | None
    action: str | None
    movement_observations: tuple[TitleObservation, ...]

    @model_validator(mode="after")
    def has_content(self):
        texts = [self.space, self.action, *(o.text for o in self.movement_observations)]
        if not any(text is not None for text in texts) or any(
            text is not None and not text.strip() for text in texts
        ):
            raise ValueError("title scene requires nonblank published content")
        ids = [o.id for o in self.movement_observations]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate title observation")
        return self


class TitleReadModel(ValueContract):
    version: Literal["relational-title-readmodel-v1"] = TITLE_CONTRACT
    scenes: tuple[TitleScene, ...]

    @model_validator(mode="after")
    def ordered_scenes(self):
        ids = [s.scene_id for s in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate title scene")
        for earlier, current in zip(self.scenes, self.scenes[1:]):
            if earlier.order >= current.order or earlier.recorded_at > current.recorded_at:
                raise ValueError("title scenes must retain publication order and time")
        return self

    def citation_ids(self):
        return [scene.scene_id for scene in self.scenes]


class TitleAnswer(ValueContract):
    title: str = Field(min_length=1, max_length=30)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("blank title")
        return value


def title_context(receipt):
    """Project a finalized body receipt; retain gaps in the original scene order."""
    scenes = []
    for order, card in enumerate(receipt["cards"], 1):
        parts = card["parts"]
        prose = {}
        for stage in ("space", "action"):
            part = parts[stage]
            if part["status"] != "returned" and part["text"]:
                raise ValueError("unaccepted text in title source")
            prose[stage] = part["text"] if part["status"] == "returned" else None
        if card["body"] != "\n".join(text for text in prose.values() if text):
            raise ValueError("title source differs from adopted body")
        observations = [
            TitleObservation(id=o["id"], text=o["text"]) for o in card["movement_observations"]
        ]
        if not any(prose.values()) and not observations:
            continue
        recorded_at = (
            card["comparison"]["context"]["current"]["recorded_at"]
            if "comparison" in card
            else card["anchor"]["event_at"]
        )
        scenes.append(
            TitleScene(
                scene_id=card["scene_id"],
                order=order,
                recorded_at=recorded_at,
                **prose,
                movement_observations=observations,
            )
        )
    return TitleReadModel(scenes=scenes)


def title_request_revision(prompt_revision, request, schema):
    return digest([TITLE_CONTRACT, prompt_revision, request, schema])


def validate_title_publication(receipt):
    """Check saved title/body binding, without today's prompts or a model call."""
    title = receipt.get("title", {})
    contract = receipt.get("title_contract")
    if contract is None:
        if title.get("request", {}).get("version") in {TITLE_CONTRACT, TITLE_WRITER_POLICY}:
            raise ValueError("missing title contract marker")
        return  # Historical publications retain their original title contract.
    if contract != TITLE_CONTRACT:
        raise ValueError("unsupported title contract")
    expected = title_context(receipt)
    request = title_publication_view(expected, title.get("writer_policy"))
    if title["request"] != request or title["content_revision"] != digest(expected):
        raise ValueError("title did not read the adopted scene parts")
    schema = TitleAnswer.model_json_schema()
    if title["response_schema"] != schema or title["request_revision"] != title_request_revision(
        title["prompt_revision"], title["request"], schema
    ):
        raise ValueError("title request binding changed")
    status = title["status"]
    if status not in {"returned", "failed", "not_requested"} or (
        (status == "not_requested") != (not request["scenes"])
    ):
        raise ValueError("title status differs from available body")
    if status != "returned":
        return  # Failed raw candidates stay in the audit, never the public title.
    answer = TitleAnswer.model_validate(json.loads(title["raw_text"]))
    if title["text"] != answer.title or title["candidate"] != answer.title:
        raise ValueError("published title differs from accepted candidate")
    review_enabled = title["review_enabled"]
    execution_review = receipt.get("execution", {}).get("semantic_review_enabled", review_enabled)
    if type(review_enabled) is not bool or execution_review != review_enabled:
        raise ValueError("title review policy changed")
    if not review_enabled:
        if title["semantic_status"] != "unverified" or "semantic_review" in title:
            raise ValueError("invalid unreviewed title")
        return
    if title["semantic_status"] != "model_reviewed":
        raise ValueError("required title review was bypassed")
    review = title["semantic_review"]
    review_request = review["request"]
    if (
        review_request["part"] != "title"
        or review_request["evidence"] != title["request"]
        or review_request["candidate"]
        != {"text": answer.title, "evidence_ids": [s["scene_id"] for s in request["scenes"]]}
        or review_request["required_evidence_ids"] != []
    ):
        raise ValueError("title review read another candidate or body")
    assessment = SemanticReview.model_validate(review["assessment"])
    used = assessment.used_evidence_ids
    if (
        review["status"] != "passed"
        or not assessment.passes
        or not used
        or len(set(used)) != len(used)
        or not set(used) <= {s["scene_id"] for s in request["scenes"]}
        or SemanticReview.model_validate(json.loads(review["raw_text"])) != assessment
    ):
        raise ValueError("published title has no matching passed review")
