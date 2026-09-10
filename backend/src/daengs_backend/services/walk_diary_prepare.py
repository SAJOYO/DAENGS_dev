"""Internal preparation boundary for the existing walk generation lifecycle.

The caller owns the short DB transaction. No generation reservation, commit, HTTP,
LLM, or separate orchestrator is introduced here.
"""

import uuid
from dataclasses import dataclass

from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.services.walk_diary_contract import require_owner
from daengs_backend.services.walk_diary_input import InputAssembly, read_input
from daengs_walk.diary_stamps import PreparedDiary, StampPolicy, prepare_stamps


@dataclass(frozen=True)
class PreparedWalkDiary:
    input: InputAssembly
    prepared: PreparedDiary


async def prepare_saved_diary(session, principal: PrincipalContext, walk_id, policy: StampPolicy):
    if principal.kind != "APP_USER":
        raise PermissionError("diary requires its walk owner")
    source = await read_input(session, uuid.UUID(principal.subject), walk_id)
    require_owner(principal, source.source)
    return PreparedWalkDiary(source, prepare_stamps(source.source, policy))
