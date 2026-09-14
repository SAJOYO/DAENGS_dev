"""Ownership and generation guards for preparation and publication."""

from pydantic import Field

from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_walk.diary_input import DiaryContract, DiaryInput, Digest, Identifier


class GenerationTicket(DiaryContract):
    walk_id: Identifier
    client_session_id: Identifier
    owner_id: Identifier
    generation: int = Field(ge=1)
    input_revision: Digest


class StaleDiaryGeneration(ValueError):
    """Discard the completion; never overwrite a newer input or generation."""


def require_owner(principal: PrincipalContext, source: DiaryInput) -> None:
    # ADMIN access to walk conditions does not grant access to private diary records.
    # This is an additional scope check, not a substitute for the repository's owned().
    if principal.kind != "APP_USER" or principal.subject != source.owner_id:
        raise PermissionError("diary requires its walk owner")


def bind_generation(
    principal: PrincipalContext, source: DiaryInput, generation: int
) -> GenerationTicket:
    """Bind a generation already reserved in the existing service/DB transaction."""
    require_owner(principal, source)
    if source.photos_status == "pending":
        raise ValueError("photo snapshot must finish before reserving generation")
    return GenerationTicket(
        walk_id=source.walk_id,
        client_session_id=source.client_session_id,
        owner_id=source.owner_id,
        generation=generation,
        input_revision=source.revision(),
    )


def require_current(
    ticket: GenerationTicket,
    principal: PrincipalContext,
    current: DiaryInput,
    current_generation: int,
) -> None:
    """Call under the completion transaction lock with a freshly read input snapshot."""
    require_owner(principal, current)
    if (
        ticket.walk_id != current.walk_id
        or ticket.client_session_id != current.client_session_id
        or ticket.owner_id != current.owner_id
        or ticket.generation != current_generation
        or ticket.input_revision != current.revision()
    ):
        raise StaleDiaryGeneration("diary input or reserved generation changed")
