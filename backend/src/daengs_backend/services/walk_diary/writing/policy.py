"""Current card writing policy and request fingerprints; no execution dependencies."""

from daengs_backend.services.walk_diary.contracts import MAX_CARDS
from daengs_backend.services.walk_diary.writing.prompts import PROMPTS
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.contracts.narrative import OBSERVATION_TEXT

MODEL = "gemini-3.1-flash-lite"
MAX_INPUT_BYTES = 32_000
TIMEOUT_SECONDS = 15.0
TITLE_RESERVE_SECONDS = 3.0
LAND_WORDS = {
    "도로": "길",
    "자연초지": "풀밭",
    "기타초지": "풀밭",
    "하천": "물길",
    "해양수": "바다",
    "활엽수림": "숲",
    "침엽수림": "숲",
    "혼효림": "숲",
    "내륙습지": "습지",
    "호소": "호수·저수지",
    "기타나지": "드러난 땅",
}


def writing_version():
    return {
        "policy": "shared-orchestration-card-writing-v3",
        "observation_text": OBSERVATION_TEXT,
        "model": MODEL,
        "prompts": {key: digest(value) for key, value in PROMPTS.items()},
        "timeout_s": TIMEOUT_SECONDS,
        "title_reserve_s": TITLE_RESERVE_SECONDS,
        "card_limit": MAX_CARDS,
        "input_bytes": MAX_INPUT_BYTES,
        "concurrency": 4,
        "land_words": LAND_WORDS,
    }
