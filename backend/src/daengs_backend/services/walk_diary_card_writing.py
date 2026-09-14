"""Card graph entry and compatibility imports; implementations live below this boundary."""

from daengs_backend.services.walk_diary_card_assembly import complete_cards, frozen_card, places_for
from daengs_backend.services.walk_diary_card_contracts import (
    ActionProse,
    CardTitle,
    CardTitles,
    CardWritingResult,
    SpaceProse,
    WritingJob,
)
from daengs_backend.services.walk_diary_card_jobs import (
    action_job,
    common_context,
    job,
    space_job,
    validate_output,
)
from daengs_backend.services.walk_diary_card_policy import (
    LAND_WORDS,
    MAX_CARDS,
    MAX_INPUT_BYTES,
    MODEL,
    TIMEOUT_SECONDS,
    TITLE_RESERVE_SECONDS,
    writing_version,
)
from daengs_backend.services.walk_diary_card_provider import (
    generate_card_prose,
)

__all__ = [
    "LAND_WORDS",
    "MAX_CARDS",
    "MAX_INPUT_BYTES",
    "MODEL",
    "TIMEOUT_SECONDS",
    "TITLE_RESERVE_SECONDS",
    "ActionProse",
    "CardTitle",
    "CardTitles",
    "CardWritingResult",
    "SpaceProse",
    "WritingJob",
    "action_job",
    "common_context",
    "complete_cards",
    "frozen_card",
    "generate_card_prose",
    "job",
    "places_for",
    "space_job",
    "validate_output",
    "write_cards",
    "writing_version",
]


async def write_cards(source, base, *, generate=None, collector=None):
    """Existing diary API entry; orchestration owns planning and execution."""
    from daengs_backend.orchestration.runtime import build_diary_orchestrator

    return await build_diary_orchestrator(
        generate=generate or generate_card_prose, collector=collector
    ).run(source, base)
