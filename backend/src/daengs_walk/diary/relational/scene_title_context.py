"""A title belongs to one adopted scene body, never the whole publication."""

from typing import Literal

from pydantic import Field, model_validator

from daengs_walk.value_contracts import Instant, ValueContract, digest

SCENE_TITLE_CONTRACT = "relational-scene-title-v1"


class SceneTitleInput(ValueContract):
    version: Literal["relational-scene-title-v1"] = SCENE_TITLE_CONTRACT
    scene_id: str = Field(min_length=1)
    recorded_at: Instant
    space: str | None = None
    action: str | None = None

    @model_validator(mode="after")
    def adopted_body(self):
        texts = [self.space, self.action]
        if not any(texts) or any(t is not None and not t.strip() for t in texts):
            raise ValueError("scene title requires nonblank adopted prose")
        return self


def scene_title_context(card):
    """Whitelist the accepted parts; originals and raw observations never enter."""
    prose = {}
    for stage in ("space", "action"):
        part = card["parts"][stage]
        status, text = part["status"], part["text"]
        if status not in {"returned", "failed", "not_requested"} or not isinstance(text, str):
            raise ValueError("invalid title source part")
        if status != "returned" and text:
            raise ValueError("unaccepted text in title source")
        if status == "returned" and not text.strip():
            raise ValueError("blank adopted title source")
        prose[stage] = text if status == "returned" else None
    if card["body"] != "\n".join(t for t in prose.values() if t):
        raise ValueError("title source differs from adopted body")
    if not any(prose.values()):
        return None
    return SceneTitleInput(
        scene_id=card["scene_id"], recorded_at=card["anchor"]["event_at"], **prose
    )


def scene_title_revision(context):
    return digest(context) if context is not None else None


def scene_title_request_revision(prompt_revision, request, schema):
    return digest([SCENE_TITLE_CONTRACT, prompt_revision, request, schema])
