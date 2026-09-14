"""Internal preparation boundary for the existing walk generation lifecycle.

The caller owns the short DB transaction. No generation reservation, commit, HTTP,
LLM, or separate orchestrator is introduced here.
"""

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.services.walk_diary.guard import require_owner
from daengs_backend.services.walk_diary.preparation.input import InputAssembly, read_input
from daengs_walk.diary.selection.stamps import PreparedDiary, StampPolicy, prepare_stamps

if TYPE_CHECKING:
    from daengs_backend.services.walk_diary.preparation.board import PreparedSavedBaseBoard


@dataclass(frozen=True)
class PreparedWalkDiary:
    input: InputAssembly
    prepared: PreparedDiary
    board: "PreparedSavedBaseBoard | None" = None


async def prepare_saved_diary(session, principal: PrincipalContext, walk_id, policy: StampPolicy):
    if principal.kind != "APP_USER":
        raise PermissionError("diary requires its walk owner")
    source = await read_input(session, uuid.UUID(principal.subject), walk_id)
    require_owner(principal, source.source)
    return PreparedWalkDiary(source, prepare_stamps(source.source, policy))
