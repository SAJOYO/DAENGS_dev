"""A facility view reference, never a client-supplied owner or replacement session state."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from daengs_backend.schemas.facility_discovery import InputModel


class FacilityPlaceRef(InputModel):
    source: str = Field(min_length=1, max_length=100)
    ref: str = Field(min_length=1, max_length=200)


class AssistantFacilityContext(InputModel):
    client_request_id: UUID
    session_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)
    visible_order: list[FacilityPlaceRef] = Field(default_factory=list, max_length=120)
    visible_selected: FacilityPlaceRef | None = None
    bookmark_commands: Literal["v1"] | None = None

    @model_validator(mode="after")
    def coherent_view(self) -> Self:
        if self.session_id is None:
            if self.expected_revision or self.visible_order or self.visible_selected:
                raise ValueError("a new facility search has no previous revision or visible cards")
        elif self.expected_revision < 1:
            raise ValueError("an existing facility view requires its revision")
        if len({(p.source, p.ref) for p in self.visible_order}) != len(self.visible_order):
            raise ValueError("visible cards must be distinct")
        return self
