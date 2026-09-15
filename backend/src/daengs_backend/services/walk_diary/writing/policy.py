"""Current writing policy; readers do not import this module."""

from daengs_backend.services.walk_diary import space_details
from daengs_backend.services.walk_diary.contracts import MAX_CARDS
from daengs_backend.services.walk_diary.model_input import LEGACY_VERSION, NARRATION_INPUT_VERSION
from daengs_backend.services.walk_diary.model_input import VERSION as INPUT_VERSION
from daengs_backend.services.walk_diary.writing.prompts import PROMPTS
from daengs_walk.diary.board.space_scene import POLICY_REVISION
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.contracts.narrative import CURRENT_OBSERVATION_TEXT, OBSERVATION_TEXT

MODEL = "gemini-3.1-flash-lite"


MAX_INPUT_BYTES = 32_000


TIMEOUT_SECONDS = 15.0


TITLE_RESERVE_SECONDS = 3.0


from daengs_walk.diary.space.semantics import LAND_WORDS


def writing_version():
    return {
        "policy": "shared-orchestration-card-writing-v11",
        "input_policy": INPUT_VERSION,
        "observation_text": CURRENT_OBSERVATION_TEXT,
        "legacy_observation_text": OBSERVATION_TEXT,
        "model": MODEL,
        "prompts": {
            key: digest(
                [
                    value,
                    INPUT_VERSION,
                    space_details.VERSION,
                    space_details.INSTRUCTION,
                    POLICY_REVISION,
                ]
                if key == "space"
                # The title projection/prompt is unchanged; its cache follows its body inputs.
                else [value, LEGACY_VERSION if key == "title" else NARRATION_INPUT_VERSION]
            )
            for key, value in PROMPTS.items()
        },
        "space_details": {
            "version": space_details.VERSION,
            "max_model_calls": space_details.MAX_MODEL_CALLS,
            "max_tool_calls": 1,
            "max_details": space_details.MAX_DETAILS,
        },
        "timeout_s": TIMEOUT_SECONDS,
        "title_reserve_s": TITLE_RESERVE_SECONDS,
        "card_limit": MAX_CARDS,
        "input_bytes": MAX_INPUT_BYTES,
        "concurrency": 4,
        "land_words": LAND_WORDS,
    }
