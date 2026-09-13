"""Public, frozen card parts. Independent writing does not change the single editor."""

from typing import Literal

from pydantic import Field, model_validator

from .diary_input import DiaryContract, Digest, Identifier, digest


class CardPart(DiaryContract):
    text: str = Field(max_length=220)
    origin: Literal["generated", "fallback"]
    action_id: Identifier | None = None
    actor_id: Identifier | None = None


class CardNarrative(DiaryContract):
    format: Literal["diary-card-narrative-v1"] = "diary-card-narrative-v1"
    content_revision: Digest
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
        parts = [self.space.text, *(a.text for a in self.actions)]
        if self.original_text is not None:
            parts.append(self.original_text)
        return "\n".join(p for p in parts if p)


def content_revision(scene_id, anchor, places, space, actions, original_text):
    """Title excluded; changing only the title cannot trigger a revision loop."""
    return digest([scene_id, anchor, places, space, actions, original_text])
