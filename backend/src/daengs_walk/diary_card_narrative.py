"""Public, frozen card parts. Independent writing does not change the single editor."""

from typing import Literal

from pydantic import Field, model_validator

from .diary_input import DiaryContract, Digest, Identifier, MaterialRef, digest

# The same confirmed observation wording is used by the base board and new cards.
# Keep these v1 meanings stable for already published observation parts.
OBSERVATION_TEXT = {
    "observed_dwell": "이 구간에서는 동선이 한곳에 모였다.",
    "observed_slow": "이 구간에서는 산책 중 다른 이동 구간보다 속도가 느려졌다.",
    "observed_fast": "이 구간에서는 산책 중 다른 이동 구간보다 속도가 빨라졌다.",
}


class CardObservation(DiaryContract):
    core: MaterialRef
    kind: Literal["observed_dwell", "observed_slow", "observed_fast"]
    subject: Literal["recording_device"] = "recording_device"
    action_meaning: Literal["not_inferred"] = "not_inferred"
    text: str = Field(min_length=1, max_length=220)

    @model_validator(mode="after")
    def confirmed_wording(self):
        if self.text != OBSERVATION_TEXT[self.kind]:
            raise ValueError("observation prose differs from its confirmed meaning")
        return self


def observation_content(core, observation):
    if observation is None:
        return None
    return CardObservation(
        core=core, kind=observation.kind, text=OBSERVATION_TEXT[observation.kind]
    )


class CardPart(DiaryContract):
    text: str = Field(max_length=220)
    origin: Literal["generated", "fallback"]
    action_id: Identifier | None = None
    actor_id: Identifier | None = None


class CardNarrative(DiaryContract):
    format: Literal["diary-card-narrative-v1"] = "diary-card-narrative-v1"
    content_revision: Digest
    observation: CardObservation | None = Field(default=None, exclude_if=lambda v: v is None)
    space: CardPart
    actions: tuple[CardPart, ...] = Field(max_length=1)
    original_text: str | None = Field(default=None, max_length=2000)
    title_origin: Literal["generated", "fallback"]
    title_based_on_content_revision: Digest

    @model_validator(mode="after")
    def separated(self):
        if not self.space.text.strip():
            raise ValueError("card requires its adopted space result")
        if self.space.action_id or self.space.actor_id:
            raise ValueError("space cannot carry an actor/action")
        if any(not a.action_id or not a.text for a in self.actions):
            raise ValueError("action requires its recorded identity and text")
        if self.title_based_on_content_revision != self.content_revision:
            raise ValueError("title belongs to another content revision")
        return self

    def body(self):
        # Never trim or paraphrase the original note, including its leading/trailing whitespace.
        parts = [
            *([self.observation.text] if self.observation else []),
            self.space.text,
            *(a.text for a in self.actions),
        ]
        if self.original_text is not None:
            parts.append(self.original_text)
        return "\n".join(p for p in parts if p)


def content_revision(scene_id, anchor, places, space, actions, observation=None):
    """Only title dependencies; preserved notes belong to the publication source revision."""
    parts = [scene_id, anchor, places, space, actions]
    if observation is not None:
        parts.append(observation)
    return digest(parts)
